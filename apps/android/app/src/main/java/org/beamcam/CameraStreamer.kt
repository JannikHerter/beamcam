package org.beamcam

import android.annotation.SuppressLint
import android.content.Context
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import android.view.Surface
import java.util.concurrent.Executor

enum class Facing(val wire: String) {
    FRONT("front"),
    BACK("back"),
    ;

    companion object {
        fun fromWire(value: String) = if (value == BACK.wire) BACK else FRONT
    }
}

data class StreamFormat(val width: Int, val height: Int, val fps: Int, val bitrate: Int)

/**
 * Camera2 frames go straight into the hardware H.264 encoder's input surface, so no pixel data
 * passes through app code. Encoded access units are delivered on the camera thread via [onFrame].
 */
class CameraStreamer(
    private val context: Context,
    private val format: StreamFormat,
    private val onFrame: (ptsMicros: Long, flags: Int, data: ByteArray) -> Unit,
    private val onError: (String) -> Unit,
) {
    private val cameraManager = context.getSystemService(CameraManager::class.java)
    private val thread = HandlerThread("camera").apply { start() }
    private val handler = Handler(thread.looper)
    private val executor = Executor { handler.post(it) }

    private var encoder: MediaCodec? = null
    private var encoderSurface: Surface? = null
    private var camera: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    @Volatile private var released = false

    /** Sensor orientation and facing of the camera currently open, for [imageRotation]. */
    @Volatile var sensorOrientation = 0
        private set
    @Volatile var frontFacing = false
        private set

    /** False while a camera is opening; [sensorOrientation] and [frontFacing] are only valid when true. */
    @Volatile var ready = false
        private set

    fun start(facing: Facing, onCameraReady: () -> Unit) = handler.post {
        try {
            startEncoder()
            openCamera(facing, onCameraReady)
        } catch (e: Exception) {
            Log.e(TAG, "start failed", e)
            onError(e.message ?: e.javaClass.simpleName)
        }
    }

    fun switchCamera(facing: Facing, onCameraReady: () -> Unit) = handler.post {
        closeCamera()
        try {
            openCamera(facing, onCameraReady)
            requestKeyframe()
        } catch (e: Exception) {
            Log.e(TAG, "switch failed", e)
            onError(e.message ?: e.javaClass.simpleName)
        }
    }

    fun requestKeyframe() = handler.post {
        encoder?.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) })
    }

    fun release() {
        handler.post {
            released = true
            closeCamera()
            try {
                encoder?.stop()
            } catch (_: IllegalStateException) {
            }
            encoder?.release()
            encoderSurface?.release()
            encoder = null
            encoderSurface = null
            thread.quitSafely()
        }
    }

    private fun startEncoder() {
        val mediaFormat = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, format.width, format.height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, format.bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, format.fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
            setInteger(MediaFormat.KEY_PRIORITY, 0)
            // Without this, a static scene can stall output on some encoders and the receiver freezes.
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 1_000_000L / format.fps * 2)
        }
        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        codec.setCallback(object : MediaCodec.Callback() {
            override fun onInputBufferAvailable(codec: MediaCodec, index: Int) = Unit

            override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
                if (released) return
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && info.size > 0) {
                    val bytes = ByteArray(info.size)
                    buffer.position(info.offset)
                    buffer.get(bytes)
                    var flags = 0
                    if (info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0) flags = flags or Protocol.FLAG_KEYFRAME
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) flags = flags or Protocol.FLAG_CONFIG
                    onFrame(info.presentationTimeUs, flags, bytes)
                }
                codec.releaseOutputBuffer(index, false)
            }

            override fun onError(codec: MediaCodec, e: MediaCodec.CodecException) {
                Log.e(TAG, "encoder error", e)
                onError("encoder: ${e.diagnosticInfo}")
            }

            override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) = Unit
        }, handler)
        codec.configure(mediaFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        encoderSurface = codec.createInputSurface()
        codec.start()
        encoder = codec
    }

    @SuppressLint("MissingPermission") // The service only starts streaming after CAMERA is granted.
    private fun openCamera(facing: Facing, onCameraReady: () -> Unit) {
        val id = pickCamera(facing) ?: throw IllegalStateException("No camera found")
        val characteristics = cameraManager.getCameraCharacteristics(id)
        sensorOrientation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        frontFacing = characteristics.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
        val fpsRange = pickFpsRange(characteristics.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES), format.fps)

        cameraManager.openCamera(id, executor, object : CameraDevice.StateCallback() {
            override fun onOpened(device: CameraDevice) {
                if (released) {
                    device.close()
                    return
                }
                camera = device
                createSession(device, fpsRange, onCameraReady)
            }

            override fun onDisconnected(device: CameraDevice) {
                device.close()
                if (camera == device) {
                    camera = null
                    if (!released) onError("Camera was taken by another app")
                }
            }

            override fun onError(device: CameraDevice, error: Int) {
                device.close()
                if (camera == device) camera = null
                if (!released) onError("Camera error $error")
            }
        })
    }

    private fun createSession(device: CameraDevice, fpsRange: Range<Int>?, onCameraReady: () -> Unit) {
        val surface = encoderSurface ?: return
        val config = SessionConfiguration(
            SessionConfiguration.SESSION_REGULAR,
            listOf(OutputConfiguration(surface)),
            executor,
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    if (released || camera != device) {
                        session.close()
                        return
                    }
                    this@CameraStreamer.session = session
                    val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                        addTarget(surface)
                        if (fpsRange != null) set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, fpsRange)
                        set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                        set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
                    }.build()
                    session.setRepeatingRequest(request, null, handler)
                    ready = true
                    onCameraReady()
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    onError("Could not configure camera")
                }
            },
        )
        device.createCaptureSession(config)
    }

    private fun closeCamera() {
        ready = false
        try {
            session?.close()
        } catch (_: Exception) {
        }
        session = null
        camera?.close()
        camera = null
    }

    private fun pickCamera(facing: Facing): String? {
        val wanted = if (facing == Facing.FRONT) CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK
        val ids = cameraManager.cameraIdList
        return ids.firstOrNull { cameraManager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == wanted }
            ?: ids.firstOrNull()
    }

    companion object {
        private const val TAG = "CameraStreamer"

        fun hasCamera(context: Context, facing: Facing): Boolean {
            val manager = context.getSystemService(CameraManager::class.java)
            val wanted = if (facing == Facing.FRONT) CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK
            return manager.cameraIdList.any { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == wanted }
        }
    }
}

/** Prefer a fixed range at [fps] (steady frame rate for video calls), else the widest one reaching it. */
fun pickFpsRange(ranges: Array<Range<Int>>?, fps: Int): Range<Int>? {
    if (ranges.isNullOrEmpty()) return null
    return ranges.firstOrNull { it.lower == fps && it.upper == fps }
        ?: ranges.filter { it.upper == fps }.minByOrNull { it.lower }
        ?: ranges.filter { it.upper >= fps }.minByOrNull { it.upper }
        ?: ranges.maxByOrNull { it.upper }
}

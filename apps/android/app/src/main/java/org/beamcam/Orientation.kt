package org.beamcam

import android.view.OrientationEventListener

/**
 * Clockwise rotation (0/90/180/270) the receiver must apply to the raw sensor image so it is upright.
 * [deviceDegrees] is the raw value from [OrientationEventListener]. Same math as the
 * JPEG_ORIENTATION example in the Camera2 docs.
 */
fun imageRotation(sensorOrientation: Int, deviceDegrees: Int, frontFacing: Boolean): Int {
    if (deviceDegrees == OrientationEventListener.ORIENTATION_UNKNOWN) return sensorOrientation % 360
    val snapped = snapToQuadrant(deviceDegrees)
    val signed = if (frontFacing) -snapped else snapped
    return ((sensorOrientation + signed) % 360 + 360) % 360
}

fun snapToQuadrant(degrees: Int): Int = ((degrees + 45) / 90 * 90) % 360

/**
 * Hysteresis so a phone held near 45° does not flip back and forth: stay in [current]
 * until the reading is clearly (more than 60°) away from it.
 */
fun stableQuadrant(current: Int, degrees: Int): Int {
    if (degrees == OrientationEventListener.ORIENTATION_UNKNOWN) return current
    val distance = Math.abs(((degrees - current) % 360 + 540) % 360 - 180)
    return if (distance > 60) snapToQuadrant(degrees) else current
}

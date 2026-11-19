package org.openwebcam

import org.junit.Assert.assertEquals
import org.junit.Test

class OrientationTest {
    @Test
    fun `back camera on a typical phone`() {
        // Sensor mounted at 90°: upright in portrait needs a 90° turn, landscape-left needs none.
        assertEquals(90, imageRotation(sensorOrientation = 90, deviceDegrees = 0, frontFacing = false))
        assertEquals(180, imageRotation(90, 90, false))
        assertEquals(0, imageRotation(90, 270, false))
    }

    @Test
    fun `front camera rotates the other way`() {
        assertEquals(270, imageRotation(sensorOrientation = 270, deviceDegrees = 0, frontFacing = true))
        assertEquals(180, imageRotation(270, 90, true))
        assertEquals(0, imageRotation(270, 270, true))
    }

    @Test
    fun `unknown orientation keeps the sensor rotation`() {
        assertEquals(90, imageRotation(90, -1, false))
    }

    @Test
    fun `quadrant only changes once clearly past the boundary`() {
        assertEquals(0, stableQuadrant(0, 50))
        assertEquals(0, stableQuadrant(0, 330))
        assertEquals(90, stableQuadrant(0, 70))
        assertEquals(270, stableQuadrant(0, 280))
        assertEquals(270, stableQuadrant(270, 320))
        assertEquals(0, stableQuadrant(270, 350))
        assertEquals(180, stableQuadrant(180, -1))
    }
}

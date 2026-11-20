package org.beamcam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AllowedComputersTest {
    @Test
    fun roundTripsNewestFirst() {
        val computers = listOf(AllowedComputer("a", "Laptop", 100), AllowedComputer("b", "Desk", 200))
        val decoded = AllowedComputers.decode(AllowedComputers.encode(computers))
        assertEquals(listOf("b", "a"), decoded.map { it.secretHash })
        assertEquals("Laptop", decoded.last().name)
    }

    @Test
    fun keepsComputersAllowedBeforeNamesWereStored() {
        val decoded = AllowedComputers.decode(null, legacyHashes = setOf("old"))
        assertEquals(listOf(AllowedComputer("old", null, 0)), decoded)
    }

    @Test
    fun storedEntryWinsOverLegacyHash() {
        val json = AllowedComputers.encode(listOf(AllowedComputer("a", "Laptop", 5)))
        assertEquals(listOf(AllowedComputer("a", "Laptop", 5)), AllowedComputers.decode(json, legacyHashes = setOf("a")))
    }

    @Test
    fun connectingAgainUpdatesNameAndTime() {
        val before = listOf(AllowedComputer("a", "old-name", 1), AllowedComputer("b", "Desk", 2))
        val after = AllowedComputers.connected(before, "a", "Laptop", 10)
        assertEquals(listOf(AllowedComputer("a", "Laptop", 10), AllowedComputer("b", "Desk", 2)), after)
    }

    @Test
    fun removesOneComputer() {
        val computers = listOf(AllowedComputer("a", "Laptop", 1), AllowedComputer("b", "Desk", 2))
        assertEquals(listOf("b"), AllowedComputers.without(computers, "a").map { it.secretHash })
    }

    @Test
    fun unreadableStorageMeansNoComputers() {
        assertTrue(AllowedComputers.decode("not json").isEmpty())
    }
}

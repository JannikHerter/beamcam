package org.beamcam

import org.junit.Assert.assertEquals
import org.junit.Test

class ClaimTest {
    @Test
    fun freePhoneIsTaken() {
        assertEquals(Claim.TAKE, decideClaim(hasActive = false, activeIdentity = null, activeAwaitingApproval = false, newIdentity = null))
        assertEquals(Claim.TAKE, decideClaim(hasActive = false, activeIdentity = null, activeAwaitingApproval = false, newIdentity = "a"))
    }

    @Test
    fun allowedComputerCancelsAStrangersPrompt() {
        assertEquals(Claim.CANCEL_PROMPT, decideClaim(hasActive = true, activeIdentity = null, activeAwaitingApproval = true, newIdentity = "a"))
    }

    @Test
    fun secondStrangerWaitsForTheFirstPrompt() {
        assertEquals(Claim.BUSY, decideClaim(hasActive = true, activeIdentity = null, activeAwaitingApproval = true, newIdentity = null))
    }

    @Test
    fun streamingComputerKeepsThePhone() {
        assertEquals(Claim.BUSY, decideClaim(hasActive = true, activeIdentity = "a", activeAwaitingApproval = false, newIdentity = "b"))
        assertEquals(Claim.BUSY, decideClaim(hasActive = true, activeIdentity = "a", activeAwaitingApproval = false, newIdentity = null))
    }

    @Test
    fun sameComputerReplacesItsOldSession() {
        assertEquals(Claim.REPLACE_OWN, decideClaim(hasActive = true, activeIdentity = "a", activeAwaitingApproval = false, newIdentity = "a"))
    }
}

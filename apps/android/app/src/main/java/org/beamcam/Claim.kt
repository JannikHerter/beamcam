package org.beamcam

/** What happens when a new session asks for the camera while another one may hold it. */
internal enum class Claim {
    /** Nobody holds the camera. */
    TAKE,

    /** The same computer reconnected; its old session gets END replaced. */
    REPLACE_OWN,

    /** An allowed computer arrived while a stranger's prompt was open; the stranger gets END busy. */
    CANCEL_PROMPT,

    /** Another computer is streaming or waiting for approval; the new one gets END busy. */
    BUSY,
}

/**
 * [activeIdentity] and [newIdentity] are the hashes of the computers' pairing secrets, null for
 * a computer that asks to pair. An open prompt must not lock out the user's own computers:
 * anyone on the Wi-Fi could otherwise keep the phone busy by asking again and again.
 */
internal fun decideClaim(hasActive: Boolean, activeIdentity: String?, activeAwaitingApproval: Boolean, newIdentity: String?): Claim = when {
    !hasActive -> Claim.TAKE
    newIdentity == null -> Claim.BUSY
    activeIdentity == newIdentity -> Claim.REPLACE_OWN
    activeAwaitingApproval -> Claim.CANCEL_PROMPT
    else -> Claim.BUSY
}

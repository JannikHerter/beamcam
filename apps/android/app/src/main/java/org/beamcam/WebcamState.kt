package org.beamcam

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

sealed interface WebcamStatus {
    data object Stopped : WebcamStatus
    data object Ready : WebcamStatus
    /** [code] is the pairing code; the computer shows the same one unless someone is in between. */
    data class AwaitingApproval(val computerName: String, val code: String) : WebcamStatus
    data class Streaming(val computerName: String) : WebcamStatus
}

data class WebcamUiState(
    val status: WebcamStatus = WebcamStatus.Stopped,
    val port: Int = Protocol.DEFAULT_PORT,
    val facing: Facing = Facing.FRONT,
    /** Last problem worth telling the user about, cleared when they dismiss it. */
    val message: String? = null,
)

/** Process-wide state shared by [WebcamService] (writer) and the UI (reader). */
object WebcamState {
    private val mutable = MutableStateFlow(WebcamUiState())
    val state: StateFlow<WebcamUiState> = mutable.asStateFlow()

    @Volatile private var pendingApproval: CompletableDeferred<Boolean>? = null

    /** True while the app is on screen; it then shows its own dialog instead of a notification. */
    @Volatile var appVisible = false

    internal fun update(transform: (WebcamUiState) -> WebcamUiState) = mutable.update(transform)

    internal fun beginApproval(computerName: String, code: String): CompletableDeferred<Boolean> {
        val deferred = CompletableDeferred<Boolean>()
        pendingApproval = deferred
        update { it.copy(status = WebcamStatus.AwaitingApproval(computerName, code)) }
        return deferred
    }

    fun respondToApproval(allow: Boolean) {
        pendingApproval?.complete(allow)
        pendingApproval = null
    }

    fun dismissMessage() = update { it.copy(message = null) }
}

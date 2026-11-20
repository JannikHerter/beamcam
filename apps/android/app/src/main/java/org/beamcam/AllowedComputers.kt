package org.beamcam

import org.json.JSONObject

/** A computer this phone allowed. [secretHash] is the SHA-256 (hex) of the secret it got when it paired. */
data class AllowedComputer(val secretHash: String, val name: String?, val lastConnectedMillis: Long)

/** How [Settings] stores allowed computers: a JSON object keyed by secret hash. */
internal object AllowedComputers {
    /** [legacyHashes] are from before names were stored; they show up without a name. Newest first. */
    fun decode(json: String?, legacyHashes: Set<String> = emptySet()): List<AllowedComputer> {
        val byHash = LinkedHashMap<String, AllowedComputer>()
        legacyHashes.forEach { byHash[it] = AllowedComputer(it, null, 0) }
        val stored = json?.let { runCatching { JSONObject(it) }.getOrNull() }
        stored?.keys()?.forEach { hash ->
            val entry = stored.optJSONObject(hash) ?: return@forEach
            byHash[hash] = AllowedComputer(hash, entry.optString("name").ifBlank { null }, entry.optLong("lastConnected"))
        }
        return byHash.values.sortedByDescending { it.lastConnectedMillis }
    }

    fun encode(computers: List<AllowedComputer>): String {
        val json = JSONObject()
        for (computer in computers) {
            json.put(
                computer.secretHash,
                JSONObject().put("name", computer.name ?: "").put("lastConnected", computer.lastConnectedMillis),
            )
        }
        return json.toString()
    }

    /** Adds [secretHash] or refreshes its name and time: computers may be renamed. */
    fun connected(computers: List<AllowedComputer>, secretHash: String, name: String, nowMillis: Long): List<AllowedComputer> =
        listOf(AllowedComputer(secretHash, name, nowMillis)) + computers.filter { it.secretHash != secretHash }

    fun without(computers: List<AllowedComputer>, secretHash: String): List<AllowedComputer> =
        computers.filter { it.secretHash != secretHash }
}

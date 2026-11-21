package ru.nightdozhor.fridge

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Small dependency-free HTTP client for the demo API.
 * Call it from a coroutine running on Dispatchers.IO.
 */
class FridgeApiClient(private val baseUrl: String) : FridgeApi {
    override suspend fun sendCalories(
        userId: String,
        date: String,
        activeCalories: Double,
        source: String
    ) {
        post(
            "/api/health",
            JSONObject()
                .put("user_id", userId)
                .put("date", date)
                .put("active_calories", activeCalories)
                .put("source", source)
        )
    }

    suspend fun createUser(name: String, password: String, goal: Int = 500): JSONObject = post(
        "/api/users",
        JSONObject()
            .put("name", name)
            .put("password", password)
            .put("active_kcal_goal", goal)
    )

    suspend fun startEnrollment(userId: String, deviceId: String = "fridge-001"): JSONObject = post(
        "/api/enrollment/start",
        JSONObject().put("user_id", userId).put("device_id", deviceId)
    )

    private fun post(path: String, body: JSONObject): JSONObject {
        val connection = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            connectTimeout = 8000
            readTimeout = 8000
        }
        connection.outputStream.use { it.write(body.toString().toByteArray()) }
        val response = (if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader().use { it.readText() }
        if (connection.responseCode !in 200..299) {
            error("API ${connection.responseCode}: $response")
        }
        return JSONObject(response)
    }
}

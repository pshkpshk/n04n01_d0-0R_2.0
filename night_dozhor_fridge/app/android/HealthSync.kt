package ru.nightdozhor.fridge

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * Reads one day's active calories from Health Connect and sends the aggregate
 * to the fridge API. The app must request READ_ACTIVE_CALORIES permission first.
 */
class HealthSync(context: Context, private val api: FridgeApi) {
    private val health = HealthConnectClient.getOrCreate(context)

    suspend fun syncDay(userId: String, day: LocalDate = LocalDate.now()) {
        val zone = ZoneId.systemDefault()
        val start = day.atStartOfDay(zone).toInstant()
        val end = day.plusDays(1).atStartOfDay(zone).toInstant()
        val response = health.readRecords(
            ReadRecordsRequest(
                recordType = ActiveCaloriesBurnedRecord::class,
                timeRangeFilter = TimeRangeFilter.between(start, end)
            )
        )
        val calories = response.records.sumOf { it.energy.inKilocalories }
        api.sendCalories(userId, day.toString(), calories, "health_connect")
    }
}

interface FridgeApi {
    suspend fun sendCalories(userId: String, date: String, activeCalories: Double, source: String)
}

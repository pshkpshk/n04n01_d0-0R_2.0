# Android bridge

`HealthSync.kt` and `FridgeApiClient.kt` form the small integration layer between a native Android app and the API. The client uses `HttpURLConnection`, so it does not require an additional HTTP library.

Add the Health Connect client dependency to the Android module:

```kotlin
implementation("androidx.health.connect:connect-client:<version>")
```

Declare the permission in `AndroidManifest.xml`:

```xml
<uses-permission android:name="android.permission.health.READ_ACTIVE_CALORIES_BURNED" />
```

At onboarding, request `ActiveCaloriesBurnedRecord::class` and explain why the application uses the value. The app should call `HealthSync.syncDay` after permission is granted and from a periodic WorkManager job. The server receives only the aggregate for a day.

Call the client from a coroutine on `Dispatchers.IO`, for example `FridgeApiClient("https://your-server.example")`. The UI can use `createUser`, `startEnrollment`, and `HealthSync.syncDay`.

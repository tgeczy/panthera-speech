plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Isolated debug research artifacts, never the release's prebuilt libraries.
val experimentalJni = providers.gradleProperty("pantheraExperimentalJni").orNull
if (experimentalJni != null) {
    gradle.taskGraph.whenReady {
        check(allTasks.none { it.name.contains("Release", ignoreCase = true) }) {
            "Experimental native libraries are for debug device checks only"
        }
    }
}

android {
    namespace = "com.pantheraspeech.tts"
    compileSdk = 35
    buildFeatures { aidl = true }

    defaultConfig {
        applicationId = "com.pantheraspeech.tts"
        minSdk = 26
        targetSdk = 35
        testInstrumentationRunner = "com.pantheraspeech.tts.EngineSmokeTest"
        versionCode = 2
        versionName = "3.0.0"

        // Native libraries are prebuilt by build_jni_so.sh. Both ABIs pass
        // the four-generation device suite; each device selects its own ABI.
        ndk {
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
        }
    }

    // Kotlin sources live under src/main/kotlin.
    sourceSets["main"].java.srcDirs("src/main/kotlin")
    sourceSets["main"].assets.srcDir(rootProject.file("../../../licenses"))
    if (experimentalJni != null) {
        sourceSets["main"].jniLibs.setSrcDirs(listOf(file(experimentalJni)))
        sourceSets["debug"].assets.srcDir(file("$experimentalJni/../assets"))
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

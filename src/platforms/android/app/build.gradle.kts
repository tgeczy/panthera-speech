plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.pantheraspeech.tts"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.pantheraspeech.tts"
        minSdk = 26
        targetSdk = 35
        testInstrumentationRunner = "com.pantheraspeech.tts.EngineSmokeTest"
        versionCode = 1
        versionName = "0.1.0"

        // The native engine (libpanthera.so) is prebuilt into src/main/jniLibs
        // by build_jni_so.sh -- Gradle just packages it, so there is no CMake
        // step here and the APK build needs no NDK/Cygwin.  armeabi-v7a only:
        // the emulator maps guest addresses as a 32-bit identity.
        ndk {
            abiFilters += "armeabi-v7a"
        }
    }

    // Kotlin sources live under src/main/kotlin.
    sourceSets["main"].java.srcDirs("src/main/kotlin")

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

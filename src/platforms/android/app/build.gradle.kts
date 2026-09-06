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
            // Both, and both are needed.  Watches and budget phones are 32-bit
            // for years yet -- a Galaxy Watch reports armeabi-v7a and nothing
            // else -- while ARMv9 phones dropped AArch32 in the silicon and
            // refuse a 32-bit APK outright (INSTALL_FAILED_NO_MATCHING_ABIS).
            // Neither ABI covers the field on its own.
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
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

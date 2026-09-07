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
        // Both ABIs are needed in the end.  Watches and budget phones are
        // 32-bit for years yet -- a Galaxy Watch reports armeabi-v7a and
        // nothing else -- while ARMv9 phones dropped AArch32 in the silicon
        // and refuse a 32-bit APK outright (INSTALL_FAILED_NO_MATCHING_ABIS).
        // Neither ABI covers the field on its own.
        ndk {
            abiFilters += listOf("armeabi-v7a")
        }
    }

    // Kotlin sources live under src/main/kotlin.
    sourceSets["main"].java.srcDirs("src/main/kotlin")

    buildTypes {
        // Tiger and Leopard pass the arm64 device suite, including Alex.
        // Keep arm64 debug-only until Snow Leopard/Lion's remaining shared
        // layouts and shims are covered too: a dual-ABI device selects arm64
        // even when its currently selected generation only works on ARMv7.
        debug {
            ndk { abiFilters += listOf("arm64-v8a") }
        }
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

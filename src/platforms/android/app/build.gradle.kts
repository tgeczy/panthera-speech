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
        // arm64 is DEBUG-ONLY until it speaks, and the reason is not caution.
        //
        // When an APK carries both ABIs, the package manager picks the
        // device's PRIMARY one, and on every dual-ABI device -- a Galaxy S22,
        // Snapdragon 8 Gen 1, which still has AArch32 -- that is arm64.  So
        // shipping a half-finished arm64 library does not "add 64-bit
        // support": it takes the working 32-bit library away from every phone
        // that had it and hands those users a crash instead.  A broken ABI in
        // the APK is worse than an absent one.
        //
        // Add it back to defaultConfig, and delete this, on the day the arm64
        // build renders an utterance.
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

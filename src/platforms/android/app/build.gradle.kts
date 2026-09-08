import groovy.json.JsonSlurper
import java.security.MessageDigest
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Isolated debug research artifacts, never the release's prebuilt libraries.
val experimentalJni = providers.gradleProperty("pantheraExperimentalJni").orNull
val legacyUnicorn = providers.gradleProperty("pantheraLegacyUnicorn").orNull == "true"
val nativeNotices = layout.buildDirectory.dir("generated/pantheraLicenses")
val stageNativeNotices = tasks.register<Sync>("stageNativeNotices") {
    from(rootProject.file("../../../licenses")) {
        if (experimentalJni == null && !legacyUnicorn) {
            include("DISTRIBUTION.txt", "Panthera-MIT.txt", "Glint-MIT.txt",
                "box86-LICENSE.txt", "box64-LICENSE.txt", "Box-component-notices.txt",
                "Emoji-BSD.txt", "Unicode.txt", "LLVM-runtime-notices.txt",
                "Kotlin-LICENSE.txt", "Kotlin-NOTICE.txt")
        }
    }
    into(nativeNotices)
}
val verifyNativeBuild = tasks.register("verifyNativeBuild") {
    doLast {
        if (experimentalJni == null && !legacyUnicorn) {
            for ((abi, runtime) in listOf("armeabi-v7a" to "box86", "arm64-v8a" to "box64")) {
                val dir = file("src/main/jniLibs/$abi")
                val metadata = dir.resolve("build.json")
                check(metadata.isFile) { "Build the $abi runtime with build_jni_so.sh first" }
                val info = JsonSlurper().parse(metadata) as Map<*, *>
                val library = dir.resolve("libpanthera.so")
                check(library.isFile && info["runtime"] == runtime && info["aac"] == "glint" && info["api"] == 26) {
                    "The normal APK requires Box + Glint for $abi at API 26"
                }
                val hash = MessageDigest.getInstance("SHA-256").digest(library.readBytes())
                    .joinToString("") { "%02x".format(it.toInt() and 255) }
                check(info["sha256"] == hash) { "$abi library changed after its recorded build" }
            }
        }
    }
}
tasks.matching { it.name == "preBuild" }.configureEach {
    dependsOn(stageNativeNotices, verifyNativeBuild)
}
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
        versionCode = 3
        versionName = "3.0.1"

        // Native libraries are prebuilt by build_jni_so.sh. Both ABIs pass
        // the four-generation device suite; each device selects its own ABI.
        ndk {
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
        }
    }

    // Kotlin sources live under src/main/kotlin.
    sourceSets["main"].java.srcDirs("src/main/kotlin")
    sourceSets["main"].assets.srcDir(nativeNotices)
    if (experimentalJni != null) {
        sourceSets["main"].jniLibs.setSrcDirs(listOf(file(experimentalJni)))
        sourceSets["debug"].assets.srcDir(file("$experimentalJni/../assets"))
    }

    // Release signing.  Android refuses to install an unsigned APK, so a
    // release is only a release once it is signed with the project's key.
    // The key lives outside the repository: a `signing.properties` beside
    // settings.gradle.kts, ignored by Git, naming it -- the same file, with
    // the same four names, that TGSpeechBox's Android build reads, so one
    // convention serves both of the maintainer's apps:
    //
    //   STORE_FILE=C:/Users/you/release.keystore
    //   STORE_PASSWORD=...
    //   KEY_ALIAS=...
    //   KEY_PASSWORD=...
    //
    // Without that file the release build still succeeds and stays unsigned,
    // which is what CI and anyone without the key should get.  The same key
    // must sign every future release, or Android treats the update as a
    // different app and refuses it: keep it backed up.
    val signingProperties = rootProject.file("signing.properties")
    if (signingProperties.isFile) {
        val keys = Properties().apply { signingProperties.inputStream().use { load(it) } }
        signingConfigs {
            create("release") {
                storeFile = file(keys.getProperty("STORE_FILE"))
                storePassword = keys.getProperty("STORE_PASSWORD")
                keyAlias = keys.getProperty("KEY_ALIAS")
                keyPassword = keys.getProperty("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (signingProperties.isFile) signingConfig = signingConfigs.getByName("release")
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

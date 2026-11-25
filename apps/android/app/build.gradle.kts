plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    // The language can be picked in the app, so every bundle install needs all translations.
    bundle {
        language {
            enableSplit = false
        }
    }
    namespace = "org.beamcam"
    compileSdk = 37

    defaultConfig {
        applicationId = "org.beamcam"
        minSdk = 28
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    // The release key comes from the environment (the release workflow, or a maintainer's shell).
    // Without it a release build is signed with the debug key: good for testing, never published,
    // because the release workflow checks the certificate.
    val releaseKeystore = System.getenv("BEAMCAM_KEYSTORE")
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storeType = "PKCS12"
                storePassword = System.getenv("BEAMCAM_KEYSTORE_PASSWORD")
                keyAlias = "beamcam"
                keyPassword = System.getenv("BEAMCAM_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    // AGP would add a dependency list encrypted for Google Play to the signing block. F-Droid
    // rejects that blob, and it keeps F-Droid's build from matching ours byte for byte.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    lint {
        // targetSdk 37 needs the local network permission flow; see docs/internals/overview.md.
        disable += "OldTargetApi"
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.compose.ui.tooling.preview)
    debugImplementation(libs.androidx.compose.ui.tooling)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
    testImplementation(libs.org.json)
}

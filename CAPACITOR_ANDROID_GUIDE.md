# 📱 Capacitor & Ionic Hybrid Android App Setup Guide
### Turn TG Unlimited Cloud (Google Drive UI) into a Native Android App

---

## 📌 1. Architecture Overview

```
┌───────────────────────────────────────────────────────────┐
│                    Android Mobile Device                  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │         Capacitor Native Android Shell (.APK)       │  │
│  │                                                     │  │
│  │  ┌───────────────────────────────────────────────┐  │  │
│  │  │   Embedded Webview UI                         │  │  │
│  │  │   • Google Drive HTML/CSS/JS                  │  │  │
│  │  │   • Lightbox Previews & Multi-Select          │  │  │
│  │  └──────────────────────┬────────────────────────┘  │  │
│  │                         │                           │  │
│  │       Native Bridge (Plugins: SAF, Downloads, Share)│  │
│  └─────────────────────────┼───────────────────────────┘  │
└────────────────────────────┼──────────────────────────────┘
                             │ REST / HTTP Streaming
                             ▼
┌───────────────────────────────────────────────────────────┐
│                 Python FastAPI Backend                     │
│          (Uvicorn + Telethon / Telegram Cloud)             │
│            e.g. https://your-server-url.com                │
└───────────────────────────────────────────────────────────┘
```

---

## 🛠️ 2. Prerequisites

Before starting, ensure you have the following installed on your computer:
1. **Node.js (LTS Version):** [https://nodejs.org](https://nodejs.org) (Includes `npm` and `npx`).
2. **Android Studio:** [https://developer.android.com/studio](https://developer.android.com/studio)
   * During installation, ensure **Android SDK**, **Android SDK Command-line Tools**, and **Android SDK Build-Tools** are checked.
3. **Java JDK (JDK 17 or JDK 21 recommended):** Bundled automatically with modern Android Studio.

---

## 🚀 3. Step-by-Step Implementation

### Step 1: Initialize NPM in the Project Root
Open PowerShell / Terminal in your project directory:
```powershell
cd "c:\Users\shishir0x\Documents\Portfolio\Tele Unlim"

# Initialize package.json if you don't have one
npm init -y
```

---

### Step 2: Install Capacitor Core & Android CLI
```powershell
npm install @capacitor/core @capacitor/cli @capacitor/android
```

---

### Step 3: Initialize Capacitor Project Configuration
Run the Capacitor initializer:
```powershell
npx cap init "Google Drive" "com.tgcloud.drive" --web-dir website
```

This will create a `capacitor.config.json` file in your root folder.

---

### Step 4: Configure `capacitor.config.json`
Open `capacitor.config.json` and adjust the settings:

```json
{
  "appId": "com.tgcloud.drive",
  "appName": "Drive",
  "webDir": "website",
  "bundledWebRuntime": false,
  "server": {
    "cleartext": true,
    "allowNavigation": [
      "*"
    ]
  },
  "android": {
    "allowMixedContent": true,
    "captureInput": true,
    "webContentsDebuggingEnabled": true
  }
}
```

> 💡 **Development vs. Production Modes:**
> * **Offline UI Mode (Default):** Bundles the HTML/CSS/JS inside the APK. The app makes API calls to your server.
> * **Live URL Mode (Instant Updates):** If you deploy your backend to a public HTTPS domain (or Cloudflare Tunnel), you can set `"server": { "url": "https://drive.yourdomain.com", "cleartext": true }`. This allows you to update the web UI anytime without having to rebuild the APK!

---

### Step 5: Add the Native Android Platform
Run:
```powershell
npx cap add android
```
*(This creates an `android/` directory containing a full native Android Studio Gradle project).*

---

### Step 6: Install Recommended Native Android Plugins
To allow the app to save downloaded files directly to the Android **Downloads** folder, use the native Camera/Gallery picker, and share files:

```powershell
# 1. Native File System Access (for saving downloads directly to phone storage)
npm install @capacitor/filesystem

# 2. Native File Picker (for picking any file/document to upload)
npm install @capawesome/capacitor-file-picker

# 3. Native Android Share Sheet
npm install @capacitor/share

# 4. Native Status Bar & Splash Screen
npm install @capacitor/status-bar @capacitor/splash-screen
```

---

### Step 7: Configure Android Network Security & Permissions
To allow the app to stream files and connect over local IP (`http://192.168.x.x:8000`) or HTTP/HTTPS:

1. Open `android/app/src/main/AndroidManifest.xml` and add these permissions above `<application>`:
   ```xml
   <uses-permission android:name="android.permission.INTERNET" />
   <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
   <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />
   ```

2. Inside the `<application ...>` tag in `AndroidManifest.xml`, enable cleartext traffic:
   ```xml
   android:usesCleartextTraffic="true"
   ```

---

### Step 8: Sync Web Assets to Android
Whenever you modify HTML, CSS, or JS in `website/`, sync the changes:
```powershell
npx cap sync
```

---

### Step 9: Open in Android Studio & Build APK
1. Open the project in Android Studio:
   ```powershell
   npx cap open android
   ```
2. Wait for Android Studio to finish indexing and Gradle sync.
3. In Android Studio's top menu:
   * Click **Build** → **Build Bundle(s) / APK(s)** → **Build APK(s)**.
4. Once the build finishes, a notification will appear at the bottom right. Click **locate** to find `app-debug.apk`.
5. Transfer `app-debug.apk` to your Android phone and install it!

---

## 🔄 4. Day-to-Day Development Workflow

```powershell
# 1. Make UI/CSS/JS edits in website/
# 2. Sync to Android project:
npx cap sync

# 3. Run on a connected Android phone or emulator:
npx cap run android
```

---

## 🛡️ 5. Key Advantages of Capacitor for this Project

* **100% Single Codebase:** Zero rewrite needed for your existing Google Drive table, grid, breadcrumbs, search, and preview lightboxes.
* **Large File Streaming:** Native Android webview handles video/audio streaming with hardware acceleration.
* **Hardware Storage Access:** With `@capacitor/filesystem`, user downloads go straight to `/storage/emulated/0/Download/`.
* **Play Store Ready:** Generates production-ready `.aab` bundles for Google Play Store release.

package com.appsflyer.onelink.appsflyeronelinkbasicapp;

import android.app.Application;
import android.content.Intent;
import android.util.Log;
import com.google.gson.Gson;
import org.json.JSONObject;
import androidx.annotation.NonNull;

import java.util.Map;
import java.util.Objects;

import com.appsflyer.AppsFlyerLib;
import com.appsflyer.attribution.AppsFlyerRequestListener;

public class AppsflyerBasicApp extends Application {
    public static final String LOG_TAG = "AppsFlyerOneLinkSimApp";
    public static final String DL_ATTRS = "dl_attrs";
    Map<String, Object> conversionData = null;

    public void onCreate() {
        super.onCreate();

        // AppsFlyer SDK Integration
        AppsFlyerLib.getInstance().setDebugLog(true);
        AppsFlyerLib.getInstance().init("sQ84wpdxRTR4RMCaE9YqS4", null, this);
        AppsFlyerLib.getInstance().start(getApplicationContext(), "sQ84wpdxRTR4RMCaE9YqS4", new AppsFlyerRequestListener() {
            @Override
            public void onSuccess() {
                // ✅ YOUR CODE UPON SUCCESS
                Log.d(LOG_TAG, "AppsFlyer SDK initialized successfully.");
            }

            @Override
            public void onError(int i, @NonNull String s) {
                // ⚠️ YOUR CODE FOR ERROR HANDLING
                Log.e(LOG_TAG, "AppsFlyer SDK initialization failed: " + s + " (code: " + i + ")");
            }
        });
    }
}

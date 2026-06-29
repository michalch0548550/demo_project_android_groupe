package com.appsflyer.onelink.appsflyeronelinkbasicapp;

import android.app.Application;

import java.util.Map;
import java.util.Objects;

public class AppsflyerBasicApp extends Application {
    public static final String LOG_TAG = "AppsFlyerOneLinkSimApp";
    public static final String DL_ATTRS = "dl_attrs";
    Map<String, Object> conversionData = null;

    public void onCreate() {
        super.onCreate();

    }
}

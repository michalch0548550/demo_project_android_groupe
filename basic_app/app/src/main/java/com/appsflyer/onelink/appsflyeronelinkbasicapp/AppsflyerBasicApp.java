package com.appsflyer.onelink.appsflyeronelinkbasicapp;

import android.app.Application;

import com.appsflyer.AppsFlyerLib;
import java.util.Map;

public class AppsflyerBasicApp extends Application {
    public static final String LOG_TAG = "AppsFlyerOneLinkSimApp";
    public static final String DL_ATTRS = "dl_attrs";
    Map<String, Object> conversionData = null;

    @Override
    public void onCreate() {
        super.onCreate();

        AppsFlyerLib.getInstance().setDebugLog(true);
        AppsFlyerLib.getInstance().init("sQ84wpdxRTR4RMCaE9YqS4", null, this);
        AppsFlyerLib.getInstance().start(this);
    }
}

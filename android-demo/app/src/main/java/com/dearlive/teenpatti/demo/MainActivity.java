package com.dearlive.teenpatti.demo;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;

/**
 * Offline demo shell: loads the bundled Teen Patti Pro replay client.
 *
 * <p>Assets are copied at build time from
 * {@code games/teen_patti_pro/client/} into {@code assets/} by the
 * demo-apk workflow — no login page, no API domain, no VPS needed.
 * Entry is {@code demo.html} (fixed-seed engine replay + demo_round.json).
 * Tapping the "live game" link opens the bundled index.html, which degrades
 * gracefully to its no-session notice when no backend is reachable.
 */
public class MainActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        // demo.html fetch()es the sibling demo_round.json via file:// URL.
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        setContentView(web);
        if (savedInstanceState == null) {
            web.loadUrl("file:///android_asset/demo.html");
        } else {
            web.restoreState(savedInstanceState);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (web != null) {
            web.saveState(outState);
        }
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}

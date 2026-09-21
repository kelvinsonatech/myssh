package com.slipkprojects.ultrasshservice.tunnel;

import android.content.Context;
import java.io.IOException;
import com.slipkprojects.ultrasshservice.logger.SkStatus;
import android.content.IntentFilter;
import com.slipkprojects.ultrasshservice.tunnel.vpn.TunnelVpnService;
import androidx.localbroadcastmanager.content.LocalBroadcastManager;
import java.util.List;
import com.slipkprojects.ultrasshservice.tunnel.vpn.VpnUtils;
import android.util.Log;
import com.slipkprojects.ultrasshservice.tunnel.vpn.TunnelState;
import android.content.Intent;
import com.slipkprojects.ultrasshservice.tunnel.vpn.TunnelVpnSettings;
import android.content.BroadcastReceiver;

import com.slipkprojects.ultrasshservice.tunnel.vpn.TunnelVpnManager;
import com.slipkprojects.ultrasshservice.config.LitronSettings;
import android.os.Handler;
import java.net.InetAddress;
import java.net.UnknownHostException;

import com.trilead.ssh2.transport.TransportManager;
import java.util.concurrent.CountDownLatch;
import com.trilead.ssh2.Connection;

import android.widget.Toast;
import java.io.StringWriter;
import java.io.PrintWriter;
import java.io.File;
import com.trilead.ssh2.KnownHosts;
import com.trilead.ssh2.ProxyData;
import com.trilead.ssh2.DynamicPortForwarder;
import com.trilead.ssh2.ConnectionMonitor;
import com.trilead.ssh2.DebugLogger;
import com.trilead.ssh2.InteractiveCallback;
import com.trilead.ssh2.ServerHostKeyVerifier;
import com.reydevz.almz.R;
import com.slipkprojects.ultrasshservice.config.PasswordCache;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.ProxyInfo;
import android.os.Build;
import androidx.preference.PreferenceManager;
import java.net.Inet4Address;
import com.slipkprojects.sockshttp.MainActivity;

public class TunnelManagerThread
        implements Runnable,
        ConnectionMonitor,
        InteractiveCallback,
        ServerHostKeyVerifier,
        DebugLogger {
  private static final String TAG = TunnelManagerThread.class.getSimpleName();

  private OnStopCliente mListener;
  private Context mContext;
  private Handler mHandler;
  private LitronSettings mConfig;
  private volatile boolean mRunning = false, mStopping = false, mStarting = false;
  private final SshKeepalive sshKeepalive = new SshKeepalive();

  private Thread udpThread;
  private UDPThread mUDPThread;

  private boolean v2rayrunning = false;

  private SharedPreferences prefs;

  private CountDownLatch mTunnelThreadStopSignal;
  // private ConnectivityManager mCmgr;

  public interface OnStopCliente {
    void onStop();
  }

  public TunnelManagerThread(Handler handler, Context context) {
    mContext = context;
    mHandler = handler;

    mConfig = new LitronSettings(context);
    prefs = mConfig.getPrefsPrivate();
  }

  public void setOnStopClienteListener(OnStopCliente listener) {
    mListener = listener;
  }

  @Override
  public void run() {
    mStarting = true;
    mTunnelThreadStopSignal = new CountDownLatch(1);
    SharedPreferences prefs = mConfig.getPrefsPrivate();
    int tunnelType = prefs.getInt(LitronSettings.TUNNELTYPE_KEY, LitronSettings.bTUNNEL_TYPE_SSH_DIRECT);

    if (tunnelType == LitronSettings.bTUNNEL_TYPE_UDP) {
      SkStatus.logInfo("<strong>Starting UDP Client</strong>");
    }else if (tunnelType == LitronSettings.bTUNNEL_TYPE_V2RAY) {
      SkStatus.logInfo("<strong>Starting V2Ray Client</strong>");
    } else {
      SkStatus.logInfo(
              "<strong>" + mContext.getString(R.string.starting_service_ssh) + "</strong>");
    }
    int tries = 0;
    while (!mStopping) {
      try {
        if (!TunnelUtils.isNetworkOnline(mContext)) {
          SkStatus.updateStateString(
                  SkStatus.SSH_AGUARDANDO_REDE, mContext.getString(R.string.state_nonetwork));

          SkStatus.logInfo(R.string.state_nonetwork);

          try {
            Thread.sleep(5000);
          } catch (InterruptedException e2) {
            stopAll();
            break;
          }
        } else {
          // Retain recent failures across connection attempts.
          // SkStatus.logInfo("<strong>" + mContext.getString(R.string.state_reconnecting)
          // + "</strong>");

          try {
            Thread.sleep(50);
          } catch (InterruptedException e2) {
            stopAll();
            break;
          }

          if(isudpmode()){
            startUDPClient();
          } else {
            startClienteSSH();
          }
          break;
        }
      } catch (Exception e) {

        SkStatus.logError(
                "<strong>" + mContext.getString(R.string.state_disconnected) + "</strong>");
        closeSSH();

        try {
          Thread.sleep(50);
        } catch (InterruptedException e2) {
          stopAll();
          break;
        }
      }

      tries++;
    }

    mStarting = false;

    if (!mStopping) {
      try {
        mTunnelThreadStopSignal.await();
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    }

    if (mListener != null) {
      mListener.onStop();
    }
  }


  public void startUDPClient() {
    SkStatus.updateStateString(SkStatus.SSH_CONECTANDO, "UDP connecting");
    // SkStatus.logInfo("<b> Tunnel Type:  </b>" + "UDP New Core");
    SkStatus.logInfo("<b>UDP Client: Core is running...</b>");
    SkStatus.logInfo("<b>UDP Client:</b> Connecting");
    SkStatus.logInfo("<b>UDP Client:</b> Getting client configuration");

    mUDPThread =
            new UDPThread(
                    mContext,
                    result -> {
                      if (result) {
                        SkStatus.updateStateString(SkStatus.SSH_CONECTADO, "UDP Established");
                        this.mConnected = true;
                        try {
                          this.startTunnelVpnService();
                        } catch (IOException e) {
                          // Handle the IOException here
                        }
                      } else {
                        this.stopTunnelVpnService();
                        SkStatus.updateStateString(SkStatus.SSH_RECONECTANDO, "UDP reconnecting");
                        SkStatus.logInfo("<b>UDP Client:</b> Reconnecting..");
                        SkStatus.logInfo("<b>UDP Client: Core is running...</b>");
                        SkStatus.logInfo("<b>UDP Client:</b> Connecting");
                        SkStatus.logInfo("<b>UDP Client:</b> Getting client configuration");
                        mUDPThread.run();
                      }
                    });
    mUDPThread.start();
  }




  public void stopAll() {
    if (mStopping) return;

    SkStatus.updateStateString(
            SkStatus.SSH_PARANDO, mContext.getString(R.string.stopping_service_ssh));
    SkStatus.logInfo("<strong>" + mContext.getString(R.string.stopping_service_ssh) + "</strong>");

    new Thread(
            new Runnable() {
              @Override
              public void run() {
                mStopping = true;

                if (mTunnelThreadStopSignal != null) mTunnelThreadStopSignal.countDown();

                if (isudpmode()) {
                  if (mUDPThread != null) {
                    mUDPThread.stopVudp();
                    mUDPThread = null;
                  }
                  if (mConnected) {
                    stopForwarder();
                  }
                  mRunning = false;
                  mStarting = false;
                  mReconnecting = false;
                } else {
                  closeSSH();
                  mRunning = false;
                  mStarting = false;
                  mReconnecting = false;
                }

                try {
                  Thread.sleep(1000);
                } catch (InterruptedException e) {
                }

                SkStatus.updateStateString(
                        SkStatus.SSH_DESCONECTADO, mContext.getString(R.string.state_disconnected));

                mRunning = false;
                mStarting = false;
                mReconnecting = false;
              }
            })
            .start();
  }

  /** Forwarder */
  protected void startForwarder(int portaLocal) throws Exception {
    if (!mConnected) {
      throw new Exception();
    }

    startForwarderSocks(portaLocal);

    startTunnelVpnService();

    new Thread(
            new Runnable() {
              @Override
              public void run() {
                while (true) {
                  if (!mConnected) break;

                  try {
                    Thread.sleep(2000);
                  } catch (InterruptedException e) {
                    break;
                  }

                }
              }
            })
            .start();
  }

  protected void stopForwarder() {
    stopTunnelVpnService();

    stopForwarderSocks();
  }

  /** Cliente SSH */
  private static final int AUTH_TRIES = 1;

  private static final int RECONNECT_TRIES = 5;

  private Connection mConnection;

  private boolean mConnected = false;

  protected void startClienteSSH() throws Exception {
    mStopping = false;
    mRunning = true;

    String servidor = mConfig.getPrivString(LitronSettings.SERVIDOR_KEY);
    int porta = Integer.parseInt(mConfig.getPrivString(LitronSettings.SERVIDOR_PORTA_KEY));
    String usuario = mConfig.getPrivString(LitronSettings.USUARIO_KEY);

    String _senha = mConfig.getPrivString(LitronSettings.SENHA_KEY);
    String senha = _senha.isEmpty() ? PasswordCache.getAuthPassword(null, false) : _senha;

    String keyPath = mConfig.getSSHKeypath();
    int portaLocal = Integer.parseInt(mConfig.getPrivString(LitronSettings.PORTA_LOCAL_KEY));

    try {

      conectar(servidor, porta);

      for (int i = 0; i < AUTH_TRIES; i++) {
        if (mStopping) {
          return;
        }

        try {
          autenticar(usuario, senha, keyPath);

          break;
        } catch (IOException e) {
          if (i + 1 >= AUTH_TRIES) {
            throw new IOException("Autenticação falhou");
          } else {
            try {
              Thread.sleep(3000);
            } catch (InterruptedException e2) {
              return;
            }
          }
        }
      }

      SkStatus.updateStateString(SkStatus.SSH_CONECTADO, "SSH connection established");
      SkStatus.logInfo(
              "<strong><html><font color='#008A00'>"
                      + mContext.getString(R.string.state_connected)
                      + "</font></html></strong>");

      startForwarder(portaLocal);
      final Connection authenticatedConnection = mConnection;
      sshKeepalive.start(
          () -> authenticatedConnection.sendIgnorePacket(),
          error -> SkStatus.logInfo("SSH keepalive write failed ("
              + error.getClass().getSimpleName() + ")"),
          25000L);

    } catch (Exception e) {
      mConnected = false;

      throw e;
    }
  }

  public synchronized void closeSSH() {
    sshKeepalive.stop();
    SharedPreferences prefs = mConfig.getPrefsPrivate();
    int tunnelType = prefs.getInt(LitronSettings.TUNNELTYPE_KEY, LitronSettings.bTUNNEL_TYPE_SSH_DIRECT);
    stopForwarder();

    if (mConnection != null) {
      SkStatus.logDebug("Stopping SSH");
      mConnection.close();
      // Keep disconnect diagnostics across reconnects.
    }
  }

  protected void conectar(String servidor, int porta) throws Exception {
    if (!mStarting) {
      throw new Exception();
    }

    SharedPreferences prefs = mConfig.getPrefsPrivate();

    // aqui deve conectar
    try {

      mConnection = new Connection(servidor, porta);

      if (mConfig.getModoDebug() && !prefs.getBoolean(LitronSettings.CONFIG_PROTEGER_KEY, false)) {
        // Desativado, pois estava enchendo o Logger
        // mConnection.enableDebugging(true, this);
        mHandler.post(
                new Runnable() {
                  @Override
                  public void run() {
                    Toast.makeText(mContext, "Debug mode enabled", Toast.LENGTH_SHORT).show();
                  }
                });
      }

      // delay sleep
      if (mConfig.getIsDisabledDelaySSH()
          || !prefs.getBoolean(LitronSettings.PROXY_USAR_DEFAULT_PAYLOAD, true)) {
        mConnection.setTCPNoDelay(true);
      }

      // proxy
      addProxy(
              prefs.getBoolean(LitronSettings.CONFIG_PROTEGER_KEY, false),
              prefs.getInt(LitronSettings.TUNNELTYPE_KEY, LitronSettings.bTUNNEL_TYPE_SSH_DIRECT),
              (!prefs.getBoolean(LitronSettings.PROXY_USAR_DEFAULT_PAYLOAD, true)
                      ? mConfig.getPrivString(LitronSettings.CUSTOM_PAYLOAD_KEY)
                      : null),
              mConfig.getPrivString(LitronSettings.CUSTOM_SNI),
              mConnection);

      // monitora a conexão
      mConnection.addConnectionMonitor(this);

      if (Build.VERSION.SDK_INT >= 23) {
        ConnectivityManager cm =
                (ConnectivityManager) mContext.getSystemService(Context.CONNECTIVITY_SERVICE);
        ProxyInfo proxy = cm.getDefaultProxy();
        if (proxy != null) {
          SkStatus.logInfo(
                  "<strong>Network Proxy:</strong> "
                          + String.format("%s:%d", proxy.getHost(), proxy.getPort()));
        }
      }

      SkStatus.updateStateString(
              SkStatus.SSH_CONECTANDO, mContext.getString(R.string.state_connecting));
      SkStatus.logInfo(R.string.state_connecting);

      mConnection.connect(this, 10 * 1000, 20 * 1000);

      mConnected = true;

    } catch (Exception e) {

      StringWriter sw = new StringWriter();
      e.printStackTrace(new PrintWriter(sw));

      String cause = e.getCause().toString();
      if (useProxy && cause.contains("Key exchange was not finished")) {
        SkStatus.logError("Proxy: connection lost");
      } else {
        SkStatus.logError("SSH: " + cause);
      }

      throw new Exception(e);
    }
  }

  /** Autenticação */
  private static final String AUTH_PUBLICKEY = "publickey",
          AUTH_PASSWORD = "password",
          AUTH_KEYBOARDINTERACTIVE = "keyboard-interactive";

  protected void autenticar(String usuario, String senha, String keyPath) throws IOException {
    if (!mConnected) {
      throw new IOException();
    }

    SkStatus.updateStateString(SkStatus.SSH_AUTENTICANDO, mContext.getString(R.string.state_auth));

    try {
      if (mConnection.isAuthMethodAvailable(usuario, AUTH_PASSWORD)) {

        SkStatus.logInfo("Authenticating with password");

        if (mConnection.authenticateWithPassword(usuario, senha)) {
          SkStatus.logInfo(
                  "<strong>" + mContext.getString(R.string.state_auth_success) + "</strong>");
        }
      }
    } catch (IllegalStateException e) {
      Log.e(TAG, "Connection went away while we were trying to authenticate", e);
    } catch (Exception e) {
      Log.e(TAG, "Problem during handleAuthentication()", e);
    }

    try {
      if (mConnection.isAuthMethodAvailable(usuario, AUTH_PUBLICKEY)
              && keyPath != null
              && !keyPath.isEmpty()) {
        File f = new File(keyPath);
        if (f.exists()) {
          if (senha.equals("")) senha = null;

          SkStatus.logInfo("Authenticating with public key");

          if (mConnection.authenticateWithPublicKey(usuario, f, senha)) {
            SkStatus.logInfo(
                    "<strong>" + mContext.getString(R.string.state_auth_success) + "</strong>");
          }
        }
      }
    } catch (Exception e) {
      Log.d(TAG, "Host does not support 'Public key' authentication.");
    }

    /*try {
    if (mConnection.authenticateWithNone(mSettings.usuario)) {
    Log.d(TAG, "Authenticate with none");
    return true;
    }
    } catch (Exception e) {
    Log.d(TAG, "Host does not support 'none' authentication.");
    }

    // TODO: Need verification

    try {
    if (mConnection.isAuthMethodAvailable(mSettings.usuario,
    AUTH_KEYBOARDINTERACTIVE)) {
    if (mConnection.authenticateWithKeyboardInteractive(
    mSettings.usuario, this))
    return true;
    }
    } catch (Exception e) {
    Log.d(TAG,
    "Host does not support 'Keyboard-Interactive' authentication.");
    }*/


    if (!mConnection.isAuthenticationComplete()) {
      SkStatus.logInfo("<strong><html><font color='#FD1C0D'>" + "Username or Password Expired " + "</font></html></strong>");
      stopAll();
      throw new IOException("It was not possible to authenticate with the data provided");
    }
  }

  // XXX: Is it right?
  @Override
  public String[] replyToChallenge(
          String name, String instruction, int numPrompts, String[] prompt, boolean[] echo)
          throws Exception {
    String[] responses = new String[numPrompts];
    for (int i = 0; i < numPrompts; i++) {
      // request response from user for each prompt
      if (prompt[i].toLowerCase().contains("password"))
        responses[i] = mConfig.getPrivString(LitronSettings.SENHA_KEY);
    }
    return responses;
  }

  /** ServerHostKeyVerifier Fingerprint */
  @Override
  public boolean verifyServerHostKey(
          String hostname, int port, String serverHostKeyAlgorithm, byte[] serverHostKey)
          throws Exception {

    String fingerPrint = KnownHosts.createHexFingerprint(serverHostKeyAlgorithm, serverHostKey);
    // int fingerPrintStatus = SSHConstants.FINGER_PRINT_CHANGED;

    SkStatus.logInfo("Finger Print: " + fingerPrint);

    Log.d(TAG, "Finger Print Type: " + "");

    return true;
  }

  /** Proxy */
  private boolean useProxy = false;

  protected void addProxy(
          boolean isProteger,
          int mTunnelType,
          String mCustomPayload,
          String mCustomSNI,
          Connection conn)
          throws Exception {

    if (mTunnelType != 0) {
      useProxy = true;

      switch (mTunnelType) {
        case LitronSettings.bTUNNEL_TYPE_SSH_DIRECT:
          if (mCustomPayload != null) {
            try {
              ProxyData proxyData =
                      new HttpProxyCustom(
                              mConfig.getPrivString(LitronSettings.SERVIDOR_KEY),
                              Integer.parseInt(mConfig.getPrivString(LitronSettings.SERVIDOR_PORTA_KEY)),
                              null,
                              null,
                              mCustomPayload,
                              true,
                              mContext);

              conn.setProxyData(proxyData);

              if (!mCustomPayload.isEmpty() && !isProteger)
                ;
              //	SkStatus.logInfo("Payload: ******");

            } catch (Exception e) {
              throw new Exception(mContext.getString(R.string.error_proxy_invalid));
            }
          } else {
            useProxy = false;
          }
          break;

        case LitronSettings.bTUNNEL_TYPE_SSH_PROXY:
          String customPayload = mCustomPayload;

          if (customPayload != null && customPayload.isEmpty()) {
            customPayload = null;
          }

          String servidor = mConfig.getPrivString(LitronSettings.PROXY_IP_KEY);
          int porta = Integer.parseInt(mConfig.getPrivString(LitronSettings.PROXY_PORTA_KEY));

          try {
            ProxyData proxyData =
                    new HttpProxyCustom(servidor, porta, null, null, customPayload, false, mContext);

            if (!isProteger)
              ;
            //	SkStatus.logInfo(String.format("Proxy Remote: ******", servidor, porta));
            conn.setProxyData(proxyData);

            if (customPayload != null && !customPayload.isEmpty() && !isProteger)
              ;
            //	SkStatus.logInfo("Payload: ******");

          } catch (Exception e) {
            SkStatus.logError(R.string.error_proxy_invalid);

            throw new Exception(mContext.getString(R.string.error_proxy_invalid));
          }
          break;

        case LitronSettings.bTUNNEL_TYPE_SSH_SSLTUNNEL:
          String customSNI = mCustomSNI;
          if (customSNI != null && customSNI.isEmpty()) {
            customPayload = null;
          }

          String sshServer = mConfig.getPrivString(LitronSettings.SERVIDOR_KEY);
          int sshPort = Integer.parseInt(mConfig.getPrivString(LitronSettings.SERVIDOR_PORTA_KEY));

          try {

            ProxyData sslTypeData = new SSLTunnelProxy(sshServer, sshPort, customSNI);
            conn.setProxyData(sslTypeData);

          } catch (Exception e) {
            SkStatus.logInfo(e.getMessage());
          }
          break;

        case LitronSettings.bTUNNEL_TYPE_PAY_SSL:
          String customSNI2 = mCustomSNI;
          if (customSNI2 != null && customSNI2.isEmpty()) {
            customSNI2 = null;
          }
          String customPayload2 = mCustomPayload;

          if (customPayload2 != null && customPayload2.isEmpty()) {
            customPayload2 = null;
          }

          String sshServer2 = mConfig.getPrivString(LitronSettings.SERVIDOR_KEY);
          int sshPort2 = Integer.parseInt(mConfig.getPrivString(LitronSettings.SERVIDOR_PORTA_KEY));

          try {

            SSLProxy sslTun = new SSLProxy(sshServer2, sshPort2, customSNI2, customPayload2);
            conn.setProxyData(sslTun);

          } catch (Exception e) {
            SkStatus.logInfo(e.getMessage());
          }

          break;

          case 5:
              String customSNI3 = mCustomSNI;
              if (customSNI3 != null && customSNI3.isEmpty()) {
                  customSNI3 = null;
              }
              String customPayload3 = mCustomPayload;

              if (customPayload3 != null && customPayload3.isEmpty()) {
                  customPayload3 = null;
              }

              String sshServer3 = mConfig.getPrivString(LitronSettings.SERVIDOR_KEY);
              int sshPort3 = Integer.parseInt(mConfig.getPrivString(LitronSettings.SERVIDOR_PORTA_KEY));
              try {
                  SSLRemoteProxy sslTun =
                          new SSLRemoteProxy(sshServer3, sshPort3, customSNI3, customPayload3);
                  conn.setProxyData(sslTun);

              } catch (Exception e) {
                  SkStatus.logInfo(e.getMessage());
              }
              break;

        case LitronSettings.bTUNNEL_TYPE_SLOWDNS:
          if (mCustomPayload != null) {
            try {
              ProxyData proxyData =
                      new HttpProxyCustom(
                              "127.0.0.1",
                              Integer.parseInt("8989"),
                              null,
                              null,
                              mCustomPayload,
                              true,
                              mContext);

              conn.setProxyData(proxyData);

              /*      if (!mCustomPayload.isEmpty() && !isProteger)
              SkStatus.logInfo("Payload" + mCustomPayload);**/

            } catch (Exception e) {
              throw new Exception(mContext.getString(R.string.error_proxy_invalid));
            }
          } else {
            useProxy = false;
          }
          break;

          /*case Prefs.TUNNEL_TYPE_SSH_HTTP:
          SkStatus.logInfo("Usando Tunnel HTTP");

          String servidorHttp = "165.227.48.122";
          int portaHttp = 80;

          ProxyData pData = new HttpTunnelCliente(servidorHttp, portaHttp);

          SkStatus.logInfo(String.format("Proxy: %s:%d", servidorHttp, portaHttp));
          conn.setProxyData(pData);
          break;*/

        default:
          useProxy = false;
      }
    }
  }

  /** Socks5 Forwarder */
  private DynamicPortForwarder dpf;

  private synchronized void startForwarderSocks(int portaLocal) throws Exception {
    if (!mConnected) {
      throw new Exception();
    }

    // SkStatus.logInfo("starting socks local");
    SkStatus.logDebug(String.format("socks local listen: %d", portaLocal));

    try {

      int nThreads = mConfig.getMaximoThreadsSocks();

      if (nThreads > 0) {
        dpf = mConnection.createDynamicPortForwarder(portaLocal, nThreads);

        SkStatus.logDebug("socks local number threads: " + Integer.toString(nThreads));
      } else {
        dpf = mConnection.createDynamicPortForwarder(portaLocal);
      }

    } catch (Exception e) {
      SkStatus.logError("Socks Local: " + e.getCause().toString());

      throw new Exception();
    }
  }

  private synchronized void stopForwarderSocks() {
    if (dpf != null) {
      try {
        dpf.close();
      } catch (IOException e) {
      }
      dpf = null;
    }
  }

  /** Connection Monitor */
  @Override
  public void connectionLost(Throwable reason) {
    if (mStarting || mStopping || mReconnecting) {
      return;
    }

    SkStatus.logError("<strong>" + mContext.getString(R.string.log_conection_lost) + "</strong>");

    if (reason != null) {
      // Messages can be null (e.g. EOF/reset exceptions); never lose reconnect.
      String message = reason.getMessage() == null ? "" : reason.getMessage();
      Throwable cause = reason;
      for (int depth = 0; depth < 4 && cause.getCause() != null; depth++) {
        cause = cause.getCause();
      }
      SkStatus.logInfo("SSH disconnect: " + reason.getClass().getSimpleName()
          + " / " + cause.getClass().getSimpleName());
      if (message.contains("There was a problem during connect")) {
        return;
      } else if (message.contains("Closed due to user request")) {
        return;
      } else if (message.contains("The connect timeout expired")) {
        return;
      }
    }

    reconnectSSH();
  }

  public volatile boolean mReconnecting = false;

  public void reconnectSSH() {
    synchronized (this) {
      if (mStarting || mStopping || mReconnecting) {
        return;
      }
      mReconnecting = true;
    }
      closeSSH();
    SkStatus.updateStateString(SkStatus.SSH_RECONECTANDO, "Reconnecting..");

    try {
      Thread.sleep(100);
    } catch (InterruptedException e) {
      mReconnecting = false;
      return;
    }

    for (int i = 0; i < RECONNECT_TRIES; i++) {
      if (mStopping) {
        mReconnecting = false;
        return;
      }

      int sleepTime = 5;
      if (!TunnelUtils.isNetworkOnline(mContext)) {
        SkStatus.updateStateString(SkStatus.SSH_AGUARDANDO_REDE, "Waiting for network..");

        SkStatus.logInfo(R.string.state_nonetwork);
      } else {
        sleepTime = 3;
        mStarting = true;
        SkStatus.updateStateString(SkStatus.SSH_RECONECTANDO, "Reconnecting..");
        // Retain the disconnect cause so device/server failures can be diagnosed.
        SkStatus.logInfo(
                "<strong>" + mContext.getString(R.string.state_reconnecting) + "</strong>");

        try {
          startClienteSSH();

          mStarting = false;
          mReconnecting = false;
          // mConnected = true;

          return;
        } catch (Exception e) {
          SkStatus.logInfo(
                  "<strong>" + mContext.getString(R.string.state_disconnected) + "</strong>");
        }

        mStarting = false;
      }

      try {
        Thread.sleep(sleepTime * 1000);
        i--;
      } catch (InterruptedException e2) {
        mReconnecting = false;
        return;
      }
    }

    mReconnecting = false;

    stopAll();
  }

  @Override
  public void onReceiveInfo(int id, String msg) {
    if (id == SERVER_BANNER) {
      SkStatus.logInfo(
              mContext.getString(R.string.log_server_banner)
                      + " <font color=\"#FFA500\">Litron Shield - ENJOY</font>");
    }
  }

  /** Debug Logger */
  @Override
  public void log(int level, String className, String message) {
    SkStatus.logDebug(String.format("%s: %s", className, message));
  }

  /** Vpn Tunnel */
  public static Inet4Address getIPv4Addresses(final InetAddress[] array) {
    for (final InetAddress inetAddress : array) {
      if (inetAddress instanceof Inet4Address) {
        return (Inet4Address) inetAddress;
      }
    }
    return null;
  }

  String serverAddr;

  protected void startTunnelVpnService() throws IOException {
    if (!mConnected) {
      throw new IOException();
    }

    SkStatus.logInfo("starting tunnel service");
    SharedPreferences mPref = PreferenceManager.getDefaultSharedPreferences(mContext);
    SharedPreferences prefs = mConfig.getPrefsPrivate();

    // Broadcast
    IntentFilter broadcastFilter =
            new IntentFilter(TunnelVpnService.TUNNEL_VPN_DISCONNECT_BROADCAST);
    broadcastFilter.addAction(TunnelVpnService.TUNNEL_VPN_START_BROADCAST);
    // Inicia Broadcast
    LocalBroadcastManager.getInstance(mContext)
            .registerReceiver(m_vpnTunnelBroadcastReceiver, broadcastFilter);

    String m_socksServerAddress =
            String.format("127.0.0.1:%s", mConfig.getPrivString(LitronSettings.PORTA_LOCAL_KEY));
    boolean m_dnsForward = mConfig.getVpnDnsForward();
    String m_udpResolver = mConfig.getVpnUdpForward() ? mConfig.getVpnUdpResolver() : null;

    String servidorIP = mConfig.getPrivString(LitronSettings.SERVIDOR_KEY);

    if (prefs.getInt(LitronSettings.TUNNELTYPE_KEY, LitronSettings.bTUNNEL_TYPE_SSH_DIRECT)
            == LitronSettings.bTUNNEL_TYPE_SSH_PROXY) {
      try {
        servidorIP = mConfig.getPrivString(LitronSettings.PROXY_IP_KEY);
      } catch (Exception e) {
        SkStatus.logError(R.string.error_proxy_invalid);

        throw new IOException(mContext.getString(R.string.error_proxy_invalid));
      }
    }

    try {
      InetAddress servidorAddr = TransportManager.createInetAddress(servidorIP);

      if (prefs.getInt(LitronSettings.TUNNELTYPE_KEY, 1) == LitronSettings.bTUNNEL_TYPE_UDP) {
        serverAddr =
                getIPv4Addresses(InetAddress.getAllByName(prefs.getString(LitronSettings.SERVIDOR_KEY, "")))
                        .getHostAddress();
      } else {
        serverAddr = servidorIP = servidorAddr.getHostAddress();
      }
    } catch (UnknownHostException e) {
      throw new IOException(mContext.getString(R.string.error_server_ip_invalid));
    }

    String[] m_excludeIps = { servidorIP };

    String[] m_dnsResolvers = null;
    if (m_dnsForward) {
      m_dnsResolvers = new String[] {mConfig.getVpnDnsResolver1()};
    } else {
      List<String> lista = VpnUtils.getNetworkDnsServer(mContext);
      m_dnsResolvers = new String[] {lista.get(0)};
    }


    if (isServiceVpnRunning()) {
      Log.d(TAG, "already running service");

      TunnelVpnManager tunnelManager = TunnelState.getTunnelState().getTunnelManager();

      if (tunnelManager != null) {
        tunnelManager.restartTunnel(m_socksServerAddress);
      }

      return;
    }

    Intent startTunnelVpn = new Intent(mContext, TunnelVpnService.class);
    startTunnelVpn.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

    TunnelVpnSettings settings =
            new TunnelVpnSettings(
                    m_socksServerAddress,
                    m_dnsForward,
                    m_dnsResolvers,
                    (m_dnsForward && m_udpResolver == null || !m_dnsForward && m_udpResolver != null),
                    m_udpResolver,
                    m_excludeIps,
                    mConfig.getIsFilterApps(),
                    mConfig.getIsFilterBypassMode(),
                    mConfig.getFilterApps(),
                    mConfig.getIsTetheringSubnet(),
                    mConfig.getBypass());
    startTunnelVpn.putExtra(TunnelVpnManager.VPN_SETTINGS, settings);

    if (mContext.startService(startTunnelVpn) == null) {
      SkStatus.logInfo("failed to start tunnel vpn service");

      throw new IOException("Failed to start Vpn Service");
    }

    TunnelState.getTunnelState().setStartingTunnelManager();
  }

  public static boolean isServiceVpnRunning() {
    TunnelState tunnelState = TunnelState.getTunnelState();
    return tunnelState.getStartingTunnelManager() || tunnelState.getTunnelManager() != null;
  }

  protected synchronized void stopTunnelVpnService() {
    if (!isServiceVpnRunning()) {
      return;
    }

    // Use signalStopService to asynchronously stop the service.
    // 1. VpnService doesn't respond to stopService calls
    // 2. The UI will not block while waiting for stopService to return
    // This scheme assumes that the UI will monitor that the service is
    // running while the Activity is not bound to it. This is the state
    // while the tunnel is shutting down.
    SkStatus.logInfo("stopping tunnel service");

    TunnelVpnManager currentTunnelManager = TunnelState.getTunnelState().getTunnelManager();

    if (currentTunnelManager != null) {
      currentTunnelManager.signalStopService();
    }

    /*if (mThreadLocation != null && mThreadLocation.isAlive()) {
    	mThreadLocation.interrupt();
    }
    mThreadLocation = null;*/

    // Parando Broadcast
    LocalBroadcastManager.getInstance(mContext).unregisterReceiver(m_vpnTunnelBroadcastReceiver);
  }

  // private Thread mThreadLocation;

  private boolean isv2raymode() {
    return prefs.getInt(LitronSettings.TUNNELTYPE_KEY, 0) == LitronSettings.bTUNNEL_TYPE_V2RAY; }

  private boolean isudpmode() {
    return prefs.getInt(LitronSettings.TUNNELTYPE_KEY, 0) == LitronSettings.bTUNNEL_TYPE_UDP; }

  // Local BroadcastReceiver
  private BroadcastReceiver m_vpnTunnelBroadcastReceiver =
          new BroadcastReceiver() {
            @Override
            public synchronized void onReceive(Context context, Intent intent) {
              final String action = intent.getAction();

              if (TunnelVpnService.TUNNEL_VPN_START_BROADCAST.equals(action)) {
                boolean startSuccess =
                        intent.getBooleanExtra(TunnelVpnService.TUNNEL_VPN_START_SUCCESS_EXTRA, true);

                if (!startSuccess) {
                  stopAll();
                }

              } else if (TunnelVpnService.TUNNEL_VPN_DISCONNECT_BROADCAST.equals(action)) {
                stopAll();
              }
            }
          };
}
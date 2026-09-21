package com.slipkprojects.ultrasshservice.tunnel;

import android.content.Context;
import android.content.SharedPreferences;

import com.slipkprojects.sockshttp.MainActivity;
import com.slipkprojects.ultrasshservice.logger.*;
import com.slipkprojects.ultrasshservice.util.securepreferences.SecurePreferences;
import com.trilead.ssh2.HTTPProxyException;
import com.trilead.ssh2.ProxyData;
import com.trilead.ssh2.crypto.Base64;
import com.trilead.ssh2.sftp.Packet;
import com.trilead.ssh2.transport.ClientServerHello;

import org.conscrypt.Conscrypt;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.channels.SocketChannel;
import java.security.SecureRandom;
import java.security.Security;
import java.util.Arrays;

import javax.net.ssl.HandshakeCompletedEvent;
import javax.net.ssl.HandshakeCompletedListener;
import javax.net.ssl.KeyManager;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import javax.net.ssl.SSLPeerUnverifiedException;
import javax.net.ssl.SSLSession;

public class SSLProxy implements ProxyData {
	private Context mContext;

	@Override
	public void close() {
		if (mSocket == null) return;

		try {
			mSocket.close();
		} catch (IOException e) {
			/* failed */
		}
	}

	class HandshakeTunnelCompletedListener implements HandshakeCompletedListener {
		private final String val$host;
		private final int val$port;
		private final SSLSocket val$sslSocket;

		HandshakeTunnelCompletedListener(String str, int i, SSLSocket sSLSocket) {
			this.val$host = str;
			this.val$port = i;
			this.val$sslSocket = sSLSocket;
		}

		public void handshakeCompleted(HandshakeCompletedEvent handshakeCompletedEvent) {
			SkStatus.logWarning(
					new StringBuffer()
							.append("Enable Protocols : ")
							.append(Arrays.toString(val$sslSocket.getSupportedProtocols()))
							.toString());
			String mSLorTls = "";
			if (dsp.getString("tls_version_min_override", "auto").equals("auto")) {
				mSLorTls = "Auto";
			} else if (dsp.getString("tls_version_min_override", "auto").equals("SSL")) {
				mSLorTls = "SSL";
			} else if (dsp.getString("tls_version_min_override", "auto").equals("TLS")) {
				mSLorTls = "TLS";
			} else if (dsp.getString("tls_version_min_override", "auto").equals("SSLv3")) {
				mSLorTls = "SSLv3";
			} else {
				mSLorTls = handshakeCompletedEvent.getSession().getProtocol();
			}
			SkStatus.logWarning("Using Protocol: " + mSLorTls);
			SkStatus.logWarning(
					"ChiperSuite : " + handshakeCompletedEvent.getSession().getCipherSuite());
		}
	}

	static {
		try {
			Security.insertProviderAt(Conscrypt.newProvider(), 1);

		} catch (NoClassDefFoundError e) {
			e.printStackTrace();
		}
	}

	private String proxyHost;
	private String proxyPass;
	private int proxyPort;
	private String proxyUser;
	private String requestPayload;
	private SecurePreferences dsp;
	private String stunnelServer;
	private int stunnelPort = 443;
	private String stunnelHostSNI;
	;
	private Socket mSocket;

	public SSLProxy(String server, int port, String hostSni, String requestPayload) {
		this.stunnelServer = server;
		this.stunnelPort = port;
		this.stunnelHostSNI = hostSni;
		this.proxyUser = null;
		this.proxyPass = null;
		dsp = MainActivity.sp;
		this.requestPayload = requestPayload;
	}

	@Override
	public Socket openConnection(String hostname, int port, int connectTimeout, int readTimeout)
			throws IOException {

		mSocket = SocketChannel.open().socket();
mSocket.setTcpNoDelay(true);
// Use the same destination as the existing TLS connection, only once.
mSocket.connect(new InetSocketAddress(hostname, port), connectTimeout);
mSocket.setSoTimeout(readTimeout);

		if (mSocket.isConnected()) {
			mSocket = doSSLHandshake(hostname, stunnelHostSNI, port);
			// SkStatus.logInfo(R.string.state_proxy_running);

			String requestPayload = getRequestPayload(hostname, port);

			OutputStream out = mSocket.getOutputStream();
			// suporte a [split] na payload
			if (!TunnelUtils.injectSplitPayload(requestPayload, out)) {
				try {
					out.write(requestPayload.getBytes("ISO-8859-1"));
				} catch (UnsupportedEncodingException e2) {
					out.write(requestPayload.getBytes());
				}
				out.flush();
			}

// Only custom payloads use the interim-response-aware handshake.
if (this.requestPayload != null) {
try {
int status = PayloadResponse.read(mSocket.getInputStream());
SkStatus.logInfo("HTTP payload handshake: " + status);
return mSocket;
} catch (IOException error) {
try { mSocket.close(); } catch (IOException ignored) {}
throw error;
}
}

			byte[] buffer = new byte[1024];
			InputStream in = mSocket.getInputStream();

			int len = ClientServerHello.readLineRN(in, buffer);

			String httpReponseFirstLine = "";
			try {
				httpReponseFirstLine = new String(buffer, 0, len, "ISO-8859-1");
			} catch (UnsupportedEncodingException e3) {
				httpReponseFirstLine = new String(buffer, 0, len);
			}

			// SkStatus.logInfo("<strong>" + httpReponseFirstLine + "</strong>");

			String str2 = httpReponseFirstLine;
			int parseInt = Integer.parseInt(str2.substring(9, 12));

			if (parseInt == 200) {
				return this.mSocket;
			} else if (parseInt == 101) {
				SkStatus.logInfo("<b>HTTP/1.1 <font color=#0062AF>POWERED BY: Litron Project</font></b>");
				return this.mSocket;
			}

			if (!httpReponseFirstLine.startsWith("HTTP/")) {
				throw new IOException("The proxy did not send back a valid HTTP response.");
			} else if (httpReponseFirstLine.length() >= 14
					&& httpReponseFirstLine.charAt(8) == ' '
					&& httpReponseFirstLine.charAt(12) == ' ') {
				try {
					int errorCode = Integer.parseInt(httpReponseFirstLine.substring(9, 12));
					if (errorCode < 0 || errorCode > 999) {
						throw new IOException("The proxy did not send back a valid HTTP response.");
					} else if (errorCode != Packet.SSH_FXP_EXTENDED) {
						throw new HTTPProxyException(httpReponseFirstLine.substring(13), errorCode);
					} else {
						return mSocket;
					}
				} catch (NumberFormatException e4) {
					throw new IOException("The proxy did not send back a valid HTTP response.");
				}
			} else {
				throw new IOException("The proxy did not send back a valid HTTP response.");
			}
		}

		return mSocket;
	}

	private String getRequestPayload(String hostname, int port) {
		String payload = this.requestPayload;

		if (payload != null) {
			payload = TunnelUtils.formatCustomPayload(hostname, port, payload);
		} else {
			StringBuffer sb = new StringBuffer();

			sb.append("CONNECT ");
			sb.append(hostname);
			sb.append(':');
			sb.append(port);
			sb.append(" HTTP/1.0\r\n");
			if (!(this.proxyUser == null || this.proxyPass == null)) {
				char[] encoded;
				String credentials = this.proxyUser + ":" + this.proxyPass;
				try {
					encoded = Base64.encode(credentials.getBytes("ISO-8859-1"));
				} catch (UnsupportedEncodingException e) {
					encoded = Base64.encode(credentials.getBytes());
				}
				sb.append("Proxy-Authorization: Basic ");
				sb.append(encoded);
				sb.append("\r\n");
			}
			sb.append("\r\n");

			payload = sb.toString();
		}

		return payload;
	}

	private Socket doSSLHandshake(Socket socket, String host, String sni, int port)
			throws IOException {
		TrustManager[] trustAllCerts =
				new TrustManager[] {
						new X509TrustManager() {
							public java.security.cert.X509Certificate[] getAcceptedIssuers() {
								return null;
							}

							public void checkClientTrusted(
									java.security.cert.X509Certificate[] certs, String authType) {}

							public void checkServerTrusted(
									java.security.cert.X509Certificate[] certs, String authType) {}
						}
				};
		try {
			X509TrustManager tm = Conscrypt.getDefaultX509TrustManager();
			SSLContext sSLContext = SSLContext.getInstance("TLS", "Conscrypt");
			KeyManager[] keyManagerArr = null;
			sSLContext.init(keyManagerArr, trustAllCerts, new SecureRandom());
			SSLSocket socket3 =
					(SSLSocket) sSLContext.getSocketFactory().createSocket(socket, host, port, true);
			if (sSLContext.getSocketFactory() instanceof android.net.SSLCertificateSocketFactory
					&& android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.JELLY_BEAN_MR1) {
				((android.net.SSLCertificateSocketFactory) sSLContext.getSocketFactory())
						.setHostname(socket, sni);
			} else {
				try {
					socket.getClass().getMethod("setHostname", String.class).invoke(socket, sni);
					SkStatus.logInfo("Setting up SNI...");
				} catch (Throwable e) {
					// ignore any error, we just can't set the hostname...
				}
			}
			socket3.setEnabledProtocols(socket3.getSupportedProtocols());
			socket3.addHandshakeCompletedListener(
					new HandshakeTunnelCompletedListener(host, port, socket3));
			SkStatus.logInfo("Starting SSL Handshake...");
			socket3.startHandshake();
			return socket3;
		} catch (Exception e) {
			IOException iOException =
					new IOException(
							new StringBuffer().append("Could not do SSL handshake").toString());
			throw iOException;
		}
	}

	private SSLSocket doSSLHandshake(String host, String sni, int port) throws IOException {
		TrustManager[] trustAllCerts = new TrustManager[] { new X509TrustManager() {
			public java.security.cert.X509Certificate[] getAcceptedIssuers() {
				return null;
			}

			public void checkClientTrusted(java.security.cert.X509Certificate[] certs, String authType) {
			}

			public void checkServerTrusted(java.security.cert.X509Certificate[] certs, String authType) {
			}
		} };
		try {
			String mSLorTls = "";
			if (dsp.getString("tls_version_min_override", "auto").equals("SSL")) {
				mSLorTls = "SSL";
			} else if (dsp.getString("tls_version_min_override", "auto").equals("TLS")) {
				mSLorTls = "TLS";
			} else if (dsp.getString("tls_version_min_override", "auto").equals("SSLv3")) {
				mSLorTls = "SSLv3";
			} else {
				mSLorTls = "TLS";
			}
			SSLContext sSLContext = SSLContext.getInstance(mSLorTls);
			//   SSLContext sSLContext = SSLContext.getInstance("TLS");
			KeyManager[] keyManagerArr = null;
			sSLContext.init(keyManagerArr, trustAllCerts, new SecureRandom());
			TLSSocketFactory tsf = new TLSSocketFactory();
SSLSocket socket = (SSLSocket) tsf.createSocket(mSocket, host, port, true);
socket.setTcpNoDelay(true);
socket.setSoTimeout(mSocket.getSoTimeout());
			try {
				socket.getClass().getMethod("setHostname", String.class).invoke(socket, sni);
			} catch (Throwable e) {
			}

			socket.addHandshakeCompletedListener(new HandshakeTunnelCompletedListener(host, port, socket));
			socket.startHandshake();
			SkStatus.logInfo("Starting SSL Handshake...");
			return socket;
		} catch (Exception e) {
try { mSocket.close(); } catch (IOException ignored) {}
IOException iOException = new IOException("Could not do SSL handshake", e);
			throw iOException;
		}}
}
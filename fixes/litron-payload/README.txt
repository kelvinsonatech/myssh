LitronShield custom-payload compatibility fix

Copy the app/ directory from this patch into your existing Android project,
merging directories and replacing the two matching Java files. Include the
new PayloadResponse.java file. Then rebuild the app in Android Studio.

Scope:
- Custom HTTP and SSL payload paths now consume complete HTTP headers.
- Interim responses such as 100 Continue are followed until 101 or 200.
- SSH banner bytes are left untouched for the SSH library.
- Rejected/malformed responses report an error rather than false success.
- Standard CONNECT, Dropbear bypass, payload tokens/splitting, TLS setup,
  authentication, other protocols and the server installer are unchanged.

Connection startup update:
- TLS now wraps the connected TCP socket instead of opening a second one.
- The effective TLS destination, SNI and TLS-version preferences are retained.
- TCP_NODELAY is enabled on HTTP/SSL payload sockets for small handshake writes.
- SSL uses the caller's connection/read timeouts and closes on handshake error.
- Explicit split delays and reconnection backoff have not been shortened.

No APK or full Android build is included. Real Android/device compatibility
has not been verified here. This fixes identified handshake defects, not a
guarantee that any particular network or host accepts a payload.

Only the modified/helper Java files are included. No signing keystore, credentials, SDK
paths, cached build outputs, or other files from your upload are distributed.
Keep a backup of the two original files before replacing them.

SSH stability update:
- Adds an SSH ignore/keepalive packet every 25 seconds after forwarding starts.
  Stops the worker on disconnect; it does not open extra sockets or poll servers.
  This may help idle NAT connections; it is not a server-health/pong check.
- Custom payload profiles retain TCP_NODELAY through the SSH transport setup.
- Handles missing disconnect error messages and unknown disconnect reasons.
- Prevents simultaneous reconnect claims and preserves recent failure logs.
- Payload delays, cryptography, authentication, and server configuration remain
  unchanged. Real Ubuntu disconnect causes still require device/server logs.
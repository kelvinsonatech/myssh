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

No APK or full Android build is included. Real Android/device compatibility
has not been verified here. This fixes identified handshake defects, not a
guarantee that any particular network or host accepts a payload.

Only three Java files are included. No signing keystore, credentials, SDK
paths, cached build outputs, or other files from your upload are distributed.
Keep a backup of the two original files before replacing them.
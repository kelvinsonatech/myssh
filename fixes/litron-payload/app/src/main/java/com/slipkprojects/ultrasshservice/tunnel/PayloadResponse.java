package com.slipkprojects.ultrasshservice.tunnel;

import java.io.IOException;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;

/** HTTP handshake only; never buffers past the final header into SSH data. */
final class PayloadResponse {
    private PayloadResponse() {}

    static int read(InputStream in) throws IOException {
        int[] remaining = {65536};
        for (int responses = 0; responses < 16; responses++) {
            String status = line(in, remaining);
            if (!status.matches("HTTP/1\\.[01] [0-9]{3}( .*)?")) {
                throw new IOException("Invalid HTTP payload response");
            }
            int code = Integer.parseInt(status.substring(9, 12));
            while (!line(in, remaining).isEmpty()) {
                // Consume this response's complete headers, not the SSH banner.
            }
            if (code >= 100 && code < 200 && code != 101) {
                continue;
            }
            if (code == 101 || code == 200) {
                return code;
            }
            throw new IOException("HTTP payload rejected: " + code);
        }
        throw new IOException("Too many interim HTTP responses");
    }

    private static String line(InputStream in, int[] remaining) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        while (true) {
            if (--remaining[0] < 0 || bytes.size() >= 8192) {
                throw new IOException("HTTP payload response headers too large");
            }
            int value = in.read();
            if (value < 0) throw new IOException("Connection closed during HTTP payload handshake");
            if (value == '\n') {
                byte[] data = bytes.toByteArray();
                if (data.length == 0 || data[data.length - 1] != '\r') {
                    throw new IOException("Invalid HTTP header line ending");
                }
                return new String(data, 0, data.length - 1, "ISO-8859-1");
            }
            bytes.write(value);
        }
    }
}
package com.slipkprojects.ultrasshservice.tunnel;

import java.io.ByteArrayInputStream;
import java.io.IOException;

public class PayloadResponseTest {
    private static void accepts(String headers, int expected) throws Exception {
        ByteArrayInputStream in = new ByteArrayInputStream(
            (headers + "SSH-2.0-test\r\n").getBytes("ISO-8859-1"));
        if (PayloadResponse.read(in) != expected) throw new AssertionError("Wrong status");
        byte[] banner = new byte[14];
        int n = in.read(banner);
        if (!new String(banner, 0, n, "ISO-8859-1").equals("SSH-2.0-test\r\n"))
            throw new AssertionError("SSH banner consumed or HTTP headers leaked");
    }

    private static void rejects(String input) throws Exception {
        try {
            PayloadResponse.read(new ByteArrayInputStream(input.getBytes("ISO-8859-1")));
            throw new AssertionError("Accepted invalid response");
        } catch (IOException expected) {}
    }

    public static void main(String[] args) throws Exception {
        accepts("HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: upgrade\r\n\r\n", 101);
        accepts("HTTP/1.1 101 Switching Protocols\r\n\r\n", 101);
        accepts("HTTP/1.0 200 Connection established\r\nProxy-Agent: test\r\n\r\n", 200);
        accepts("HTTP/1.1 103 Early Hints\r\n\r\nHTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 101 Switching Protocols\r\n\r\n", 101);
        rejects("HTTP/1.1 403 Forbidden\r\n\r\n");
        rejects("HTTP/1.1 301 Moved\r\n\r\n");
        rejects("bad\r\n\r\n");
        rejects("HTTP/1.1 101 Switching Protocols\r\nUpgrade:");
        rejects("HTTP/1.1 100 Continue\r\n\r\n");
        rejects("HTTP/1.1 101 Switching Protocols\n\n");
        StringBuilder excess = new StringBuilder();
        for (int i = 0; i < 17; i++) excess.append("HTTP/1.1 100 Continue\r\n\r\n");
        rejects(excess.toString());
        System.out.println("PASS: 11 handshake cases; SSH banner preserved.");
    }
}
package com.slipkprojects.ultrasshservice.tunnel;

import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class SshKeepaliveTest {
    public static void main(String[] args) throws Exception {
        SshKeepalive worker = new SshKeepalive();
        AtomicInteger sends = new AtomicInteger();
        AtomicInteger failures = new AtomicInteger();
        CountDownLatch sent = new CountDownLatch(2);
        worker.start(() -> { sends.incrementAndGet(); sent.countDown(); },
            e -> failures.incrementAndGet(), 20);
        if (!sent.await(2, TimeUnit.SECONDS)) throw new AssertionError("No keepalive");
        worker.stop();
        Thread.sleep(60);
        int stopped = sends.get();
        Thread.sleep(60);
        if (sends.get() != stopped || failures.get() != 0) throw new AssertionError("Stop failed");
        CountDownLatch error = new CountDownLatch(1);
        worker.start(() -> { throw new IOException("test"); },
            e -> { failures.incrementAndGet(); error.countDown(); }, 20);
        if (!error.await(2, TimeUnit.SECONDS)) throw new AssertionError("Failure not reported");
        Thread.sleep(60);
        if (failures.get() != 1) throw new AssertionError("Failure loop");
        CountDownLatch restarted = new CountDownLatch(1);
        worker.start(() -> restarted.countDown(), e -> {}, 20);
        if (!restarted.await(2, TimeUnit.SECONDS)) throw new AssertionError("Restart failed");
        worker.stop();
        CountDownLatch closed = new CountDownLatch(1);
        worker.start(() -> { throw new IllegalStateException("closed"); },
            e -> closed.countDown(), 20);
        if (!closed.await(2, TimeUnit.SECONDS)) throw new AssertionError("Close race failed");
        worker.stop();
        System.out.println("PASS: keepalive send, stop, failure, restart, closed-session race.");
    }
}
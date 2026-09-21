package com.slipkprojects.ultrasshservice.tunnel;

import java.io.IOException;

/** One interruptible worker per authenticated SSH session; no extra sockets. */
final class SshKeepalive {
    interface Sender { void send() throws IOException; }
    interface Failure { void report(IOException error); }
    private Thread worker;

    synchronized void start(Sender sender, Failure failure, long intervalMs) {
        stop();
        if (intervalMs <= 0) throw new IllegalArgumentException("intervalMs");
        worker = new Thread(() -> {
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(intervalMs);
                    if (Thread.currentThread().isInterrupted()) return;
                    sender.send();
                }
            } catch (InterruptedException stopped) {
                Thread.currentThread().interrupt();
            } catch (IOException error) {
                if (!Thread.currentThread().isInterrupted()) failure.report(error);
            } catch (IllegalStateException closed) {
                if (!Thread.currentThread().isInterrupted())
                    failure.report(new IOException("SSH session no longer available", closed));
            }
        }, "SSH-keepalive");
        worker.setDaemon(true);
        worker.start();
    }

    synchronized void stop() {
        if (worker != null) {
            worker.interrupt();
            worker = null;
        }
    }
}
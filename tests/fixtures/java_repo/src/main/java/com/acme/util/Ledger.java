package com.acme.util;

public class Ledger {
    private double balance;

    public void record(double amount) {
        balance += amount;
    }

    public int size() {
        return 1;
    }
}

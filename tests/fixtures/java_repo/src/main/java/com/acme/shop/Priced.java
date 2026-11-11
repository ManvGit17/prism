package com.acme.shop;

/** Anything with a price. */
public interface Priced {
    double price();

    default String label() {
        return "priced";
    }
}

package com.acme.shop;

/** A thing for sale. */
public class Item implements Priced {
    private final String name;
    private final double price;

    public Item(String name, double price) {
        this.name = name;
        this.price = price;
    }

    /** The unit price. */
    @Override
    public double price() {
        return price;
    }

    public String name() {
        return name;
    }

    public static Item of(String name) {
        return new Item(name, 1.0);
    }
}

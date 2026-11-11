package com.acme.shop;

import com.acme.util.*;

public class DiscountItem extends Item {
    public DiscountItem(String name, double price) {
        super(name, price);
    }

    @Override
    public double price() {
        return super.price() * 0.9;
    }

    public String describe() {
        return name() + " " + Money.format(price());
    }
}

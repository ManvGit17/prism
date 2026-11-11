package com.acme.shop;

import static com.acme.util.Money.round;

import com.acme.util.Ledger;
import java.util.ArrayList;
import java.util.List;

/** A shopping cart. */
public class Cart {
    private final List<Item> items = new ArrayList<>();
    private final Ledger ledger;

    public Cart(Ledger ledger) {
        this.ledger = ledger;
    }

    public void add(Item item) {
        items.add(item);
    }

    public void add(Item item, int quantity) {
        for (int i = 0; i < quantity; i++) {
            add(item);
        }
    }

    public int size() {
        return items.size();
    }

    public double total() {
        double sum = items.stream().mapToDouble(Item::price).sum();
        return round(sum);
    }

    public double checkout(List<Line> lines) {
        Priced bonus = new Gift();
        lines.forEach(line -> line.amount());
        ledger.record(total() + bonus.price());
        System.out.println(Item.of("receipt").name());
        return total();
    }

    static double scale(double value, int quantity) {
        return value * quantity;
    }

    /** One line of an order. */
    public static class Line {
        private final Item item;
        private final int quantity;

        Line(Item item, int quantity) {
            this.item = item;
            this.quantity = quantity;
        }

        double amount() {
            return scale(item.price(), quantity);
        }
    }
}

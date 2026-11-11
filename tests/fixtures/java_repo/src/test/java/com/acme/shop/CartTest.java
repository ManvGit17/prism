package com.acme.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class CartTest {
    private Cart newCart() {
        return new Cart(new com.acme.util.Ledger());
    }

    @Test
    void totalSumsItems() {
        Cart cart = newCart();
        cart.add(new Item("pen", 2.0));
        assertEquals(2.0, cart.total());
    }

    @ParameterizedTest
    @ValueSource(ints = {1, 2})
    void addWithQuantity(int quantity) {
        Cart cart = newCart();
        cart.add(new Item("pen", 1.0), quantity);
        assertEquals(quantity, cart.size());
    }
}

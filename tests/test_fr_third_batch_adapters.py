from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from airco_tracker.adapters.base import is_presale_delivery
from airco_tracker.adapters.fr.h2r import _parse_card
from airco_tracker.adapters.fr.mon_camping_car import _category_product_urls, _parse_product_page as parse_mon
from airco_tracker.adapters.fr.narbonne import _parse_product_page as parse_narbonne
from airco_tracker.adapters.fr.obelink import _category_product_urls as obelink_urls
from airco_tracker.adapters.fr.obelink import _parse_product_page as parse_obelink


class FrenchThirdBatchAdapterTests(unittest.TestCase):
    def test_h2r_card_uses_current_price_and_marks_sur_commande_presale(self) -> None:
        soup = BeautifulSoup(
            """
            <div class="product-miniature">
              <h2 class="product-name">
                <a href="/climatisation-pour-camping-car/20418-eurom-ac2401.html">
                  EUROM Climatiseur AC3201E
                </a>
              </h2>
              <span class="regular-price">699,00 €</span>
              <span class="price product-price">479,00 €</span>
              <div class="product-availability">
                <span>Sur commande, dispo sous 20 à 25 jours</span>
              </div>
            </div>
            """,
            "html.parser",
        )

        product = _parse_card(soup.select_one(".product-miniature"), "https://www.h2r-equipements.com/")

        assert product is not None
        self.assertEqual(product.name, "EUROM Climatiseur AC3201E")
        self.assertEqual(product.price_eur, 479.0)
        self.assertTrue(product.available)
        self.assertTrue(product.presale)

    def test_obelink_reads_itemlist_urls_and_product_availability(self) -> None:
        urls = obelink_urls(
            """
            <script type="application/ld+json">
            {"@context":"https://schema.org","@graph":[{"@type":"ItemList","itemListElement":[
              {"@type":"ListItem","item":{"@type":"Product","name":"Obelink CA-3000 Climatiseur split",
               "description":"Capacité de refroidissement: 820 W","url":"https://www.obelink.fr/ca.html/"}},
              {"@type":"ListItem","item":{"@type":"Product","name":"Filtre climatiseur",
               "description":"Accessoire","url":"https://www.obelink.fr/filter.html/"}}
            ]}]}
            </script>
            """
        )
        self.assertEqual(urls, ["https://www.obelink.fr/ca.html"])

        product = parse_obelink(
            """
            <script type="application/ld+json">
            {"@context":"https://schema.org","@graph":[{"@type":"Product",
              "name":"Obelink CA-3000 Climatiseur split",
              "description":"Capacité de refroidissement: 820 W",
              "url":"https://www.obelink.fr/ca.html",
              "offers":{"@type":"Offer","availability":"https://schema.org/InStock",
              "price":399,"priceCurrency":"EUR"}}]}
            </script>
            """,
            "https://www.obelink.fr/ca.html",
        )
        self.assertTrue(product.available)
        self.assertEqual(product.price_eur, 399.0)

    def test_narbonne_ignores_schema_stock_when_home_delivery_is_unavailable(self) -> None:
        product = parse_narbonne(
            """
            <script type="application/ld+json">
            {"@context":"https://schema.org/","@type":"Product",
              "name":"Climatiseur de toit Plein-Aircon 12V",
              "description":"Puissance frigorifique 4100 BTU",
              "offers":{"@type":"Offer","url":"https://shop.test/p","price":"1679.00",
              "availability":"https://schema.org/InStock"}}
            </script>
            <div class="stock_web">
              <div class="ttl">Livraison à Domicile :</div>
              <div class="content">Indisponible <div>Retrait magasin uniquement</div></div>
            </div>
            """,
            "https://shop.test/p",
        )

        self.assertFalse(product.available)
        self.assertEqual(product.delivery, "Livraison à Domicile : Indisponible Retrait magasin uniquement")

    def test_mon_camping_car_backorder_is_available_presale_not_immediate(self) -> None:
        urls = _category_product_urls(
            """
            <div class="desktop-product-list-container">
              <a href="/climatiseur-portable-ecoflow-wave-3-ecoflow.html">
                <h3 class="product-designation-button">Climatiseur portable EcoFlow WAVE 3 - ECOFLOW</h3>
              </a>
            </div>
            <div class="desktop-product-list-container">
              <a href="/bac-a-condensats-0-5-l-pundmann.html">
                <h3 class="product-designation-button">Bac à condensats 0,5 L - ARCTIX - PUNDMANN</h3>
              </a>
            </div>
            """,
            "https://www.mon-camping-car.com/categorie-climatiseurs-portable-1.html",
        )
        self.assertEqual(urls, ["https://www.mon-camping-car.com/climatiseur-portable-ecoflow-wave-3-ecoflow.html"])

        product = parse_mon(
            """
            <h1>Climatiseur portable EcoFlow WAVE 3 - ECOFLOW</h1>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@graph":[{"@type":"Product",
              "name":"climatiseur portable ecoflow wave 3 - ecoflow",
              "description":"6100 BTU",
              "offers":{"@type":"Offer","price":"849.00","priceCurrency":"EUR",
              "url":"https://www.mon-camping-car.com/wave.html",
              "availability":"https://schema.org/BackOrder"}}]}
            </script>
            <span class="text-secondary">Disponible à partir du 20/07/2026</span>
            <button class="add-cart-button">Ajouter au panier</button>
            """,
            "https://www.mon-camping-car.com/wave.html",
        )

        self.assertTrue(product.available)
        self.assertTrue(product.presale)
        self.assertEqual(product.delivery, "Disponible à partir du 20/07/2026")

    def test_french_sur_commande_is_a_presale_marker(self) -> None:
        self.assertTrue(is_presale_delivery("Sur commande, dispo sous 4 à 6 jours"))


if __name__ == "__main__":
    unittest.main()

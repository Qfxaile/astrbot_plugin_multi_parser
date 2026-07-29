from astrbot_multi_parser.core.product_metadata import (
    ProductMetadata,
    extract_json_ld_product,
    extract_open_graph_product,
    format_product_price,
    iter_json_script_values,
)


def test_product_metadata_fills_only_missing_fields():
    primary = ProductMetadata(title="主标题", price="¥99.00")
    fallback = ProductMetadata(
        title="备用标题",
        price="¥88.00",
        shop="测试店铺",
        image_url="https://img.example.com/main.jpg",
    )

    assert primary.with_fallback(fallback) == ProductMetadata(
        title="主标题",
        price="¥99.00",
        shop="测试店铺",
        image_url="https://img.example.com/main.jpg",
    )


def test_extract_json_ld_product_reads_graph_offer_seller_and_image():
    html = """
    <script type="application/ld+json">
      {"@graph":[{"@type":"Product","name":" 测试 &amp; 商品 ",
      "image":["//img.example.com/main.jpg"],
      "offers":{"price":"199.00","priceCurrency":"CNY"},
      "seller":{"name":"官方旗舰店"}}]}
    </script>
    """

    assert extract_json_ld_product(
        html,
        "https://shop.example.com/item/1",
    ) == ProductMetadata(
        title="测试 & 商品",
        price="¥199.00",
        shop="官方旗舰店",
        image_url="https://img.example.com/main.jpg",
    )


def test_extract_json_ld_product_combines_multiple_product_nodes():
    html = """
    <script type="application/ld+json">
      [
        {"@type":["Thing","Product"],"name":"首个商品"},
        {"@type":"Product","offers":[{"lowPrice":"18.5"}],
         "seller":{"name":"后备店铺"},
         "image":{"url":"/images/main.jpg"}}
      ]
    </script>
    """

    assert extract_json_ld_product(
        html,
        "https://shop.example.com/item/1",
    ) == ProductMetadata(
        title="首个商品",
        price="¥18.5",
        shop="后备店铺",
        image_url="https://shop.example.com/images/main.jpg",
    )


def test_extract_open_graph_product_reads_public_card_fields():
    html = """
    <meta property="og:title" content=" OG &amp; 商品 ">
    <meta property="og:image" content="//img.example.com/og.jpg">
    <meta property="product:price:amount" content="88.00">
    <meta property="product:price:currency" content="CNY">
    <meta property="og:site_name" content="不是店铺">
    """

    assert extract_open_graph_product(
        html,
        "https://shop.example.com/item/1",
    ) == ProductMetadata(
        title="OG & 商品",
        price="¥88.00",
        image_url="https://img.example.com/og.jpg",
    )


def test_iter_json_script_values_reads_only_valid_json_scripts():
    html = """
    <script type="application/json">{"item":{"title":"商品"}}</script>
    <script id="__NEXT_DATA__" type="application/json; charset=utf-8">
      {"page":2}
    </script>
    <script>window.secret = {"ignored": true}</script>
    <script type="application/json">not-json</script>
    """

    assert list(iter_json_script_values(html)) == [
        {"item": {"title": "商品"}},
        {"page": 2},
    ]


def test_invalid_json_ld_is_ignored_without_hiding_later_product():
    html = """
    <script type="application/ld+json">not-json</script>
    <script type="application/ld+json">
      {"@type":"Product","name":"可用商品"}
    </script>
    """

    assert extract_json_ld_product(html, "https://shop.example.com/").title == (
        "可用商品"
    )


def test_format_product_price_handles_currency_without_double_prefix():
    assert format_product_price("￥12.00", "CNY") == "￥12.00"
    assert format_product_price("12.00", "USD") == "USD 12.00"
    assert format_product_price("", "CNY") == ""

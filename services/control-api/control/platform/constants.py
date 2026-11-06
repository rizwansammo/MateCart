from dataclasses import asdict, dataclass

PRESETS = {
    "fashion": ("Fashion & Apparel", "fashion-editorial"),
    "grocery": ("Grocery & Essentials", "grocery-quickshop"),
    "electronics": ("Electronics & Gadgets", "electronics-spec-first"),
    "general": ("General / Multi-category", "general-marketplace"),
    "books": ("Bookstore & Booksellers", "books-literary"),
}


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    name: str
    presets: tuple[str, ...] = tuple(PRESETS)
    available: bool = True
    core: bool = False
    requires: tuple[str, ...] = ()


MODULES = (
    ModuleSpec("catalog", "Product catalogue", core=True),
    ModuleSpec("inventory", "Inventory", core=True),
    ModuleSpec("content_pages", "Pages and policies", core=True),
    ModuleSpec("customer_accounts", "Customer accounts", core=True),
    ModuleSpec("size_guides", "Size guides", ("fashion", "general")),
    ModuleSpec(
        "batch_expiry", "Batches and expiry", ("grocery", "general"), requires=("inventory",)
    ),
    ModuleSpec(
        "serial_numbers", "Serial numbers", ("electronics", "general"), requires=("inventory",)
    ),
    ModuleSpec("book_details", "Book details", ("books", "general")),
    ModuleSpec("bilingual_content", "Bangla and English content"),
    ModuleSpec("product_comparison", "Product comparison", ("electronics", "general"), False),
    ModuleSpec("cart_checkout", "Cart and checkout"),
    ModuleSpec("delivery_slots", "Delivery slots", ("grocery",), requires=("cart_checkout",)),
    ModuleSpec("preorders", "Pre-orders", ("books", "general"), requires=("cart_checkout",)),
    ModuleSpec("cash_on_delivery", "Cash on delivery", requires=("cart_checkout",)),
    ModuleSpec("online_payments", "Online payments", requires=("cart_checkout",)),
    ModuleSpec(
        "emi", "EMI instalments", ("electronics", "general"), False, requires=("online_payments",)
    ),
    ModuleSpec("courier_booking", "Courier booking"),
    ModuleSpec("product_reviews", "Product reviews"),
    ModuleSpec("returns_exchanges", "Returns and exchanges"),
    ModuleSpec(
        "digital_downloads", "Digital downloads", ("books", "general"), requires=("cart_checkout",)
    ),
    ModuleSpec("flash_sales", "Flash sales", requires=("cart_checkout",)),
    ModuleSpec("loyalty", "Loyalty points", requires=("cart_checkout", "customer_accounts")),
    ModuleSpec("live_chat", "Live chat"),
    ModuleSpec("ai_assistant", "AI assistant"),
)
MODULE_BY_KEY = {item.key: item for item in MODULES}


def capabilities() -> dict[str, object]:
    return {
        "version": 1,
        "presets": [
            {"id": key, "name": value[0], "default_theme": value[1]}
            for key, value in PRESETS.items()
        ],
        "themes": [
            {"id": value[1], "preset": key, "name": value[0]} for key, value in PRESETS.items()
        ],
        "modules": [asdict(item) for item in MODULES],
    }

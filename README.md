# Amazon Price Monitor Bot 🛒

Professional Telegram bot for tracking Amazon product prices with intelligent folder organization and automatic notifications.

## ✨ Features

### 📊 Smart Price Tracking
- **Automatic price checks every 10 minutes** with instant notifications on changes
- **Discounted price detection** – always captures the lowest available price (deals, vouchers, AOD offers)
- **Manual refresh** – Update all prices on-demand with one click
- **Price history graphs** – Beautiful matplotlib charts showing trends over time
- **Multi-currency support** – Handles £, €, $, and other currencies with comma/dot decimals
- **Price trend analytics** – See products with recent price drops or increases

### 🗂️ Folder Organization
- **Create custom folders** with names and emojis to organize your tracked products
- **Categorize products** during adding or move them later between folders
- **View totals per folder** – See total price across products in each category
- **Track uncategorized items** separately for flexibility

### 🔔 Notifications & Alerts
- **Real-time price change alerts** (configurable per product)
- **Bulk alert management** – Enable/disable notifications for all products at once
- **Minimum delta threshold** (£0.01) to avoid spam
- **Direction indicators** (↑/↓) with old price, new price, and delta
- **Auto-refresh graphs** sent with each notification

### 🔍 Search & Filter
- **Search by product name** – Find products by title with case-insensitive matching
- **Search by ASIN** – Quickly locate specific products by Amazon identifier
- **Price range filter** – Show only products within your specified price range
- **Smart results display** – Search results include total value and product counts

### 📊 Statistics & Analytics
- **Product overview** – Total tracked products, items with prices, and aggregate value
- **Alert status** – See how many products have notifications enabled
- **Price trends** – View recent price drops and increases
- **Top movers** – Identify products with the biggest price changes

### 🎯 User-Friendly Interface
- **Enhanced main menu** – Organized 2x2 grid layout for easy navigation
- **Product name and clickable URL** displayed in detail view
- **Beautiful inline keyboard menus** with emojis
- **Intuitive navigation** with smart back buttons
- **FSM-based flows** for smooth user experience
- **Clean chat management** (auto-deletes old graphs)
- **Comprehensive help** – Built-in help menu explaining all features

### 💾 Data Management
- **Export functionality** – Export all tracked products with complete details
- **Data clearing** – Option to remove all data with confirmation prompt
- **Bulk operations** – Manage multiple products efficiently

### ⚡ Performance
- **Async HTTP fetching** (aiohttp) with retry/backoff and 3 attempts
- **Smart marketplace canonicalization** to UK domain for consistency
- **Random user-agent rotation** to avoid blocks
- **Rate limiting** (60s per product) with minimal delta threshold
- **Strikethrough price filtering** – Ignores list prices, only captures actual prices
- **Graceful shutdown** handling

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Telegram Bot Token (get from [@BotFather](https://t.me/botfather))

### Installation

```powershell
# Clone the repository
git clone https://github.com/yourusername/amazon-price-monitor-bot.git
cd amazon-price-monitor-bot

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows PowerShell
# OR: source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your TELEGRAM_BOT_TOKEN

# Run the bot
python -m app.bot.bot
```

## 🎮 Usage

### Main Menu Options
- **➕ Add Product** – Start tracking a new Amazon product
- **📂 My Folders** – Manage your folder organization
- **📦 All Products** – View all tracked products with total price
- **🔄 Refresh All** – Manually update all prices immediately
- **📊 Statistics** – View product statistics and price trends
- **🔍 Search** – Search products by name, ASIN, or filter by price
- **⚙️ Settings** – Manage notifications and export data
- **ℹ️ Help** – View detailed help and feature information

## 📱 Workflows

### Adding a Product
1. Click **Add Product**
2. Choose a folder (or create a new one, or select "No Folder")
3. Send the Amazon product URL
4. Receive confirmation with current price and price history graph

### Managing Folders
1. Click **My Folders**
2. View all folders with product counts
3. Create new folders with custom names and emojis
4. View products within each folder
5. Delete folders (products remain, moved to uncategorized)

### Product Management
When viewing a product:
- **🔔/🔕 Toggle Alerts** – Turn price change notifications on/off
- **🔄 Refresh Price** – Manually check for price updates
- **📂 Move to Folder** – Organize product into a different folder
- **🗑️ Remove** – Stop tracking this product

### Viewing Statistics
1. Click **Statistics** from the main menu
2. View overview: total products, total value, alerts enabled
3. See recent price drops and increases
4. Identify best deals and trends in your tracked products

### Search & Filter
1. Click **Search** from the main menu
2. Choose search method:
   - **Search by Name** – Find products containing specific text
   - **Search by ASIN** – Locate products by Amazon Standard Identification Number
   - **Filter by Price** – Show products within a price range (e.g., "10-50")
3. View matching products with total value

### Settings & Export
1. Click **Settings** from the main menu
2. **Notification Settings** – Enable/disable alerts for all products at once
3. **Export Products** – Get a formatted list of all tracked products with details
4. **Clear All Data** – Remove all tracked products and folders (with confirmation)

## 🗺️ Menu Structure

```
Main Menu
├── ➕ Add Product → Choose Folder → Enter URL
├── 📂 My Folders → View/Create/Delete Folders
├── 📦 All Products → View All → Product Details
├── 🔄 Refresh All → Update All Prices
├── 📊 Statistics → View Trends & Overview
├── 🔍 Search
│   ├── 🔤 Search by Name
│   ├── 🏷️ Search by ASIN
│   └── 💰 Filter by Price
├── ⚙️ Settings
│   ├── 🔔 Notification Settings → Toggle All Alerts
│   ├── 📤 Export Products → Get Formatted List
│   └── 🗑️ Clear All Data → Confirm Delete
└── ℹ️ Help → Feature Guide
```

## ⚙️ Configuration

Create a `.env` file (copy from `.env.example`):

```env
TELEGRAM_BOT_TOKEN=123456:ABCDEFYourBotToken

# Optional settings:
TELEGRAM_PRIMARY_CHAT_ID=123456789  # Restrict bot to specific chat (leave empty for no restriction)
PRICE_REFRESH_MINUTES=10  # Background price check interval (default: 10 minutes)
```

## 🗄️ Architecture

### Project Structure
```
app/
├── bot/
│   ├── bot.py          # Main router and handlers
│   ├── keyboards.py    # Inline keyboard builders
│   └── states.py       # FSM states
├── core/
│   ├── config.py       # Environment configuration
│   ├── db.py          # SQLite connection and schema
│   ├── folders.py     # Folder CRUD operations
│   └── products.py    # Product and price history operations
├── utils/
│   ├── fetch.py       # Amazon scraping (price, title, ASIN)
│   └── graph.py       # Price history chart generation
└── data/
    └── tracker.db     # SQLite database (auto-created)
```

### Database Schema
- **folders** – User-created categories with emoji customization
- **products** – Tracked items with ASIN, URL, title, price, folder assignment
- **price_history** – Timestamped price snapshots for trending

## 🛠️ Key Technologies
- **aiogram 3.x** – Modern async Telegram Bot framework
- **aiohttp** – Async HTTP client for fast concurrent fetching
- **BeautifulSoup4** – HTML parsing for Amazon scraping
- **matplotlib** – Price trend graph generation
- **SQLite** – Lightweight embedded database
- **structlog** – Structured logging for production monitoring

## 📝 License

MIT License - feel free to use and modify!

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🐛 Issues

Found a bug or have a feature request? Please open an issue on GitHub.

---

Built with ❤️ for smart Amazon shoppers

## ✅ Recent Enhancements

### Menu Design Improvements
- **Enhanced main menu** with 2x2 grid layout for better visual organization
- **New Statistics view** showing product overview and price trends
- **Comprehensive Help section** with feature explanations
- **Settings menu** for managing preferences and data

### New Features Added
- ✅ **Search by product name** – Find products quickly by title
- ✅ **Search by ASIN** – Locate specific products by identifier
- ✅ **Price range filtering** – Filter products by price
- ✅ **Bulk alert toggle** – Enable/disable all notifications at once
- ✅ **Product export** – Export all tracked products with details
- ✅ **Statistics dashboard** – View product counts, total value, and trends
- ✅ **Price trend analysis** – See recent price drops and increases
- ✅ **Clear all data** option with confirmation

## Future Extensibility Ideas
- Target price alerts per product (notify when price drops below threshold)
- Price drop percentage thresholds
- Export price history to CSV format
- Webhook mode for better scalability
- Admin dashboard with statistics
- Share folders between users

## License
Personal use.

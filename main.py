import os
import re
from datetime import datetime, timezone

import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import httpx
import json
import random
import uuid
from pathlib import Path


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MARKETPLACE_API_URL = os.getenv("MARKETPLACE_API_URL")
MARKETPLACE_API_KEY = os.getenv("MARKETPLACE_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from .env"
    )

if not MARKETPLACE_API_URL:
    raise RuntimeError(
        "MARKETPLACE_API_URL is missing from .env"
    )

if not MARKETPLACE_API_KEY:
    raise RuntimeError(
        "MARKETPLACE_API_KEY is missing from .env"
    )

MARKETPLACE_API_URL = MARKETPLACE_API_URL.rstrip("/")


# ============================================================
# TEST SERVER
# ============================================================

TEST_GUILD_ID = 1543964932200996914

# Azo's main server
MAIN_GUILD_ID = 1470611899065565480

# Guilds that get an instant slash-command sync.
# Any guild the bot can't reach is skipped with a
# warning instead of crashing startup.
COMMAND_GUILD_IDS = (
    TEST_GUILD_ID,
    MAIN_GUILD_ID,
)


# ============================================================
# API CLIENT
# ============================================================

class MarketplaceAPI:

    def __init__(self):

        self.client = httpx.AsyncClient(
            base_url=MARKETPLACE_API_URL,
            headers={
                "X-API-Key": MARKETPLACE_API_KEY
            },
            timeout=httpx.Timeout(
                15.0,
                connect=10.0
            )
        )

    async def close(self):

        if not self.client.is_closed:
            await self.client.aclose()


    async def health(self):

        response = await self.client.get(
            "/api/public/bot/health"
        )

        response.raise_for_status()

        return response.json()


    async def get_config(self, server_id):

        response = await self.client.get(
            f"/api/public/bot/config/{server_id}"
        )

        if response.status_code == 404:
            return {}

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):

            # Some APIs return:
            # {"config": {...}}
            if isinstance(data.get("config"), dict):
                return data["config"]

            return data

        return {}


    async def save_config(self, server_id, data):

        response = await self.client.put(
            f"/api/public/bot/config/{server_id}",
            json=data
        )

        response.raise_for_status()

        return response.json()


    async def patch_config(self, server_id, data):
        """
        Save individual config keys.

        The backend may not support PATCH (or the config
        record may not exist yet on first save), so do a
        read-merge-write via PUT instead. This creates the
        config on first save and updates single keys after.
        """
        current = {}

        try:

            current = await self.get_config(server_id)

        except Exception as e:

            print(f"[CONFIG READ] {e}")

        if not isinstance(current, dict):

            current = {}

        current.update(data)

        response = await self.client.put(
            f"/api/public/bot/config/{server_id}",
            json=current
        )

        response.raise_for_status()

        # Also persist locally so settings survive even
        # if the backend strips unknown keys.

        gid = str(int(server_id))

        if gid not in _bot_config:

            _bot_config[gid] = {}

        _bot_config[gid].update(data)

        save_bot_config(_bot_config)

        return response.json()


    async def list_ads(self, server_id, limit=100):

        response = await self.client.get(
            "/api/public/bot/ads",
            params={
                "server_id": str(server_id),
                "limit": limit
            }
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):
            return data.get("ads", [])

        if isinstance(data, list):
            return data

        return []


    async def create_ad(self, data):

        response = await self.client.post(
            "/api/public/bot/ads",
            json=data
        )

        response.raise_for_status()

        return response.json()


    async def get_ad(self, ad_id):

        response = await self.client.get(
            f"/api/public/bot/ads/{ad_id}"
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()

        return response.json()


    async def update_ad(self, ad_id, data):

        response = await self.client.patch(
            f"/api/public/bot/ads/{ad_id}",
            json=data
        )

        response.raise_for_status()

        return response.json()


    async def complete_ad(self, ad_id):

        response = await self.client.post(
            f"/api/public/bot/ads/{ad_id}/complete"
        )

        response.raise_for_status()

        return response.json()


    async def delete_ad(self, ad_id):

        response = await self.client.delete(
            f"/api/public/bot/ads/{ad_id}"
        )

        response.raise_for_status()


    async def list_tickets(self, server_id):

        response = await self.client.get(
            "/api/public/bot/tickets",
            params={
                "server_id": str(server_id)
            }
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):
            return data.get("tickets", [])

        if isinstance(data, list):
            return data

        return []


    async def create_ticket(self, data):

        response = await self.client.post(
            "/api/public/bot/tickets",
            json=data
        )

        response.raise_for_status()

        return response.json()


    async def get_ticket(self, ticket_id):

        response = await self.client.get(
            f"/api/public/bot/tickets/{ticket_id}"
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()

        return response.json()


    async def update_ticket(self, ticket_id, data):

        response = await self.client.patch(
            f"/api/public/bot/tickets/{ticket_id}",
            json=data
        )

        response.raise_for_status()

        return response.json()


    async def close_ticket(self, ticket_id):

        response = await self.client.post(
            f"/api/public/bot/tickets/{ticket_id}/close"
        )

        response.raise_for_status()

        return response.json()


api = MarketplaceAPI()


# ============================================================
# MM DEALS (local JSON persistence)
# ============================================================

MM_DEALS_FILE = Path("mm_deals.json")

def load_mm_deals():
    if MM_DEALS_FILE.exists():
        try:
            return json.loads(MM_DEALS_FILE.read_text())
        except Exception as e:
            print(f"[MM DEALS LOAD] {e}")
    return {}

def save_mm_deals(data):
    try:
        MM_DEALS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[MM DEALS SAVE] {e}")

_mm_deals = load_mm_deals()


# ============================================================
# BOT CONFIG (local persistence fallback)
# ============================================================

BOT_CONFIG_FILE = Path("bot_config.json")

def load_bot_config():
    if BOT_CONFIG_FILE.exists():
        try:
            return json.loads(BOT_CONFIG_FILE.read_text())
        except Exception as e:
            print(f"[BOT CONFIG LOAD] {e}")
    return {}

def save_bot_config(data):
    try:
        BOT_CONFIG_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[BOT CONFIG SAVE] {e}")

_bot_config = load_bot_config()


# Channels the bot is about to create via /mm, so the
# auto-detect handler knows to skip them (avoid double
# posting the deal flow).
_pending_mm_channels = set()


def _find_ticket_opener(channel):
    """Find the ticket opener from channel overwrites.

    Tickety adds the opener as a member overwrite with
    view access. We pick the first non-bot member who
    can view the channel.
    """
    for target, ow in getattr(
        channel,
        "overwrites",
        {}
    ).items():
        if (
            isinstance(target, discord.Member)
            and target.id != bot.user.id
            and ow.view_channel
        ):
            return target
    return None


# ============================================================
# HELPERS
# ============================================================

def money(value):

    try:
        return f"${float(value):,.2f} USD"

    except (TypeError, ValueError):
        return str(value)


def safe_name(value):

    value = re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower()
    )

    return value[:12] or "user"


def ticket_channel_name(buyer, seller):

    return (
        f"ticket-{safe_name(buyer)}-{safe_name(seller)}"
    )[:100]


async def get_server_config(guild_id):

    backend = {}

    try:

        backend = await api.get_config(
            guild_id
        )

    except Exception as e:

        print(
            f"[API] Failed to get config: {e}"
        )

    if not isinstance(backend, dict):

        backend = {}

    # Local overrides so settings persist even if the
    # backend strips unknown keys.

    local = _bot_config.get(
        str(int(guild_id)),
        {}
    )

    merged = {}

    merged.update(backend)

    merged.update(local)

    return merged


def configured_channel(guild, channel_id):

    if not channel_id:
        return "❌ Not configured"

    try:

        channel_id = int(channel_id)

    except (TypeError, ValueError):

        return "⚠️ Invalid channel ID"

    channel = guild.get_channel(
        channel_id
    )

    if channel:
        return channel.mention

    return "⚠️ Channel not found"


async def safe_error(
    interaction,
    message
):

    try:

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception as e:

        print(
            f"[ERROR RESPONSE] {e}"
        )


# ============================================================
# LOCKED CHANNEL + MM HELPERS
# ============================================================

def locked_channel_ids(config):
    """Return the set of locked channel IDs from config.

    Supports the new multi-channel 'locked_channel_ids'
    (comma-separated) and the old single 'locked_channel_id'.
    """
    cfg = config or {}
    ids = set()
    raw = cfg.get("locked_channel_ids")
    if raw:
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                try:
                    ids.add(int(part))
                except ValueError:
                    pass
    single = cfg.get("locked_channel_id")
    if single:
        try:
            ids.add(int(single))
        except (TypeError, ValueError):
            pass
    return ids


def is_locked_channel(channel, config):
    """A channel where only the bot may post. Everyone
    else's messages are auto-deleted."""

    if channel.id in locked_channel_ids(config):
        return True

    # Note: channels inside the MM Ticket Category are
    # NOT auto-locked anymore — that deleted every
    # message in every ticket (including Tickety's
    # support- tickets), blocking chat. Only channels
    # whose name starts with the MM prefix (created by
    # /mm) are auto-locked so the deal UI stays clean.

    # Middleman ticket channels (created by Tickety
    # or /mm) are always locked so only the deal UI
    # stays visible. The prefix is configurable in
    # /setup.
    name = getattr(channel, "name", "") or ""

    prefix = str(
        (config or {}).get("mm_ticket_prefix")
        or "need-middleman-"
    )

    if name.startswith(prefix):
        return True

    return False


def locked_channel_mentions(guild, config):
    """Human-readable list of locked channels for embeds."""
    ids = locked_channel_ids(config)
    if not ids:
        return "❌ None configured"
    mentions = []
    for cid in sorted(ids):
        ch = guild.get_channel(cid)
        if ch:
            mentions.append(ch.mention)
        else:
            mentions.append(f"⚠️ unknown ({cid})")
    return ", ".join(mentions)


def locked_channel_defaults(guild, config):
    """Pre-select currently locked channels in the dropdown."""
    out = []
    for cid in sorted(locked_channel_ids(config)):
        ch = guild.get_channel(cid)
        if ch is None:
            continue
        try:
            out.append(
                discord.SelectDefaultValue.from_channel(ch)
            )
        except Exception:
            pass
    return out


async def cached_config_safe(guild_id):
    try:
        return await cached_config(guild_id)
    except Exception:
        return {}


def mm_deal_embed(deal):
    """Build the deal confirmation card."""

    item = deal.get("item") or "—"
    price = deal.get("price") or "—"
    payment = deal.get("payment_method") or "—"

    header = f"{item} | {money(price)} | {payment}"

    names = deal.get("names", {})
    confirmed = deal.get("confirmed", {})

    lines = []

    for uid in deal.get("participants", []):
        name = names.get(uid, f"<@{uid}>")
        status = "Confirmed ✅" if confirmed.get(uid) else "Unconfirmed ❌"
        lines.append(f"{name}: {status}")

    description = (
        f"**{header}**\n\n"
        + "\n".join(lines)
        + "\n\nPlease confirm the trade by pressing the "
        "'Confirm' button below. If this deal is not "
        "accurate, please click 'Edit Deal'"
    )

    return discord.Embed(description=description, color=discord.Color.blue())


# ============================================================
# AD TEXT (plain, searchable messages)
# ============================================================

NEGOTIATE_LINE = (
    "💬 You can negotiate the deal privately "
    "here in this ticket."
)


def ad_action_words(ad_type):

    if ad_type == "WTB":
        return "wants to buy"

    return "wants to sell"


def format_ad_text(
    mention,
    item,
    price,
    ad_type
):
    """
    Plain-text ad so the item name is searchable in Discord.

    Example:
        @Azo wants to buy Inverted AWP at $105.00
    """

    return (
        f"{mention} "
        f"{ad_action_words(ad_type)} "
        f"**__{item}__** at **__{money(price)}__**"
    )


def create_ad_text(
    interaction,
    item,
    price,
    ad_type
):

    return format_ad_text(
        interaction.user.mention,
        item,
        price,
        ad_type
    )


def create_ad_text_from_data(
    guild,
    ad
):

    owner_id = ad.get(
        "owner_id"
    )

    member = None

    if owner_id:

        try:

            member = guild.get_member(
                int(owner_id)
            )

        except (TypeError, ValueError):

            pass

    if member:
        mention = member.mention

    else:
        mention = f"<@{owner_id}>"

    return format_ad_text(
        mention,
        ad.get("item", "Unknown"),
        ad.get("price"),
        ad.get("ad_type")
    )


# ============================================================
# BOT
# ============================================================

class SDBSTBot(commands.Bot):

    def __init__(self):

        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents
        )

        self.views_restored = False


    async def setup_hook(self):

        # Copy global commands to the configured guilds
        # so slash commands update instantly there.
        #
        # A guild the bot isn't in (or was invited to
        # without the applications.commands scope) will
        # raise 403 Missing Access. That must NOT kill
        # startup, so each guild is handled separately.

        # Commands are published GLOBALLY only.
        # Clear any leftover guild-scoped copies so only
        # the single global set remains.
        try:
            await self.add_cog(StockCog(self))
        except Exception as e:
            print(f"[STOCK COG] {e}")

        for guild_id in COMMAND_GUILD_IDS:

            guild = discord.Object(
                id=guild_id
            )

            try:

                self.tree.clear_commands(
                    guild=guild
                )

                await self.tree.sync(
                    guild=guild
                )

                print(
                    f"[SYNC] Cleared duplicate guild "
                    f"commands in {guild_id}."
                )

            except discord.Forbidden:

                print(
                    f"[SYNC] Missing access to guild "
                    f"{guild_id}. Skipping."
                )

            except Exception as e:

                print(
                    f"[SYNC] Cleanup failed for guild "
                    f"{guild_id}: {e}"
                )

        # Publish commands globally so ANY server the bot
        # joins gets them automatically.

        try:

            global_synced = await self.tree.sync()

            print(
                f"Synced {len(global_synced)} slash "
                f"command(s) globally."
            )

        except Exception as e:

            print(
                f"[SYNC] Global sync failed: {e}"
            )

        # IMPORTANT:
        # Do NOT call wait_until_ready() here.
        #
        # setup_hook runs BEFORE the Discord gateway
        # becomes ready. Waiting for ready here causes
        # a startup deadlock.
        #
        # Persistent views are restored from on_ready().


    async def on_ready(self):

        print(
            f"Logged in as {self.user}"
        )

        print(
            f"Bot ID: {self.user.id}"
        )

        # Test backend connection.

        try:

            health = await api.health()

            print(
                "Lovable backend: ONLINE"
            )

            print(
                f"Backend response: {health}"
            )

        except Exception as e:

            print(
                f"Lovable backend: OFFLINE ({e})"
            )

        # Restore persistent views only once.

        if not self.views_restored:
            self.views_restored = True
            await restore_persistent_views()
            await restore_mm_views()
            await restore_stock_views()


    async def close(self):

        print(
            "Shutting down..."
        )

        await api.close()

        await super().close()


bot = SDBSTBot()


# ============================================================
# STICKY NOTE CONFIG
# ============================================================

# Defaults used when the server hasn't written its own text.
DEFAULT_STICKY_TEXTS = {
    "buying_channel_id": (
        "📌 **/wtb** — USE THIS COMMAND TO POST BUYING ADS"
    ),
    "selling_channel_id": (
        "📌 **/wts** — USE THIS COMMAND TO POST SELLING ADS"
    ),
    "sticky_channel_id": (
        "📌 **Stickied Message:** use the marketplace "
        "commands to post your ad."
    ),
}

# Which config keys can hold a sticky channel.
STICKY_CHANNEL_KEYS = (
    "buying_channel_id",
    "selling_channel_id",
    "sticky_channel_id",
)

# Per-channel custom text keys.
STICKY_TEXT_KEYS = {
    "buying_channel_id": "sticky_text_buying",
    "selling_channel_id": "sticky_text_selling",
    "sticky_channel_id": "sticky_text_custom",
}


def sticky_enabled(config):

    value = (config or {}).get(
        "sticky_enabled",
        "true"
    )

    return str(value).strip().lower() in (
        "1",
        "true",
        "yes",
        "on"
    )


def sticky_text_for_key(config, key):

    custom = (config or {}).get(
        STICKY_TEXT_KEYS[key]
    )

    if custom and str(custom).strip():
        return str(custom).strip()

    return DEFAULT_STICKY_TEXTS[key]


def sticky_text_for_channel(config, channel_id):
    """
    Returns the sticky text for this channel,
    or None if the channel has no sticky.
    """

    if not sticky_enabled(config):
        return None

    for key in STICKY_CHANNEL_KEYS:

        raw = (config or {}).get(key)

        if not raw:
            continue

        try:

            if int(raw) == int(channel_id):
                return sticky_text_for_key(config, key)

        except (TypeError, ValueError):
            continue

    return None


# ============================================================
# SETUP VIEW
# ============================================================

def channel_default(guild, channel_id):
    """
    Turn a saved channel ID into a Discord default value
    so the selector shows what's already configured.
    """

    if not channel_id:
        return []

    try:

        channel_id = int(channel_id)

    except (TypeError, ValueError):

        return []

    channel = guild.get_channel(
        channel_id
    )

    if channel is None:
        return []

    try:

        return [
            discord.SelectDefaultValue.from_channel(
                channel
            )
        ]

    except Exception:

        return []


class ConfigChannelSelect(discord.ui.ChannelSelect):

    def __init__(
        self,
        parent,
        key,
        placeholder,
        channel_types,
        row=None
    ):

        self.parent_view = parent
        self.config_key = key

        select_kwargs = {
            "placeholder": placeholder,
            "channel_types": channel_types,
            "min_values": 1,
            "max_values": 1,
            "default_values": channel_default(
                parent.guild,
                parent.config.get(key)
            )
        }
        if row is not None:
            select_kwargs["row"] = row
        super().__init__(**select_kwargs)


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.user.guild_permissions.administrator:

            await safe_error(
                interaction,
                "❌ Administrator permissions required."
            )

            return

        await self.parent_view.save(
            interaction,
            self.config_key,
            self.values[0].id
        )


class StickyTextModal(discord.ui.Modal):

    def __init__(self, parent, key, label):

        super().__init__(
            title="📌 Sticky Message"
        )

        self.parent_view = parent
        self.config_key = STICKY_TEXT_KEYS[key]

        self.text_input = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.paragraph,
            default=sticky_text_for_key(
                parent.config,
                key
            ),
            max_length=1800,
            required=True
        )

        self.add_item(
            self.text_input
        )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        await self.parent_view.save(
            interaction,
            self.config_key,
            self.text_input.value.strip()
        )


class StickyTextButton(discord.ui.Button):

    def __init__(self, parent, key, label, emoji):

        self.parent_view = parent
        self.sticky_key = key

        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            row=2
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.user.guild_permissions.administrator:

            await safe_error(
                interaction,
                "❌ Administrator permissions required."
            )

            return

        await interaction.response.send_modal(
            StickyTextModal(
                self.parent_view,
                self.sticky_key,
                self.label
            )
        )


class StickyToggleButton(discord.ui.Button):

    def __init__(self, parent):

        self.parent_view = parent

        on = sticky_enabled(parent.config)

        super().__init__(
            label=(
                "Sticky Notes: ON"
                if on
                else "Sticky Notes: OFF"
            ),
            emoji="📌",
            style=(
                discord.ButtonStyle.success
                if on
                else discord.ButtonStyle.danger
            ),
            row=1
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.user.guild_permissions.administrator:

            await safe_error(
                interaction,
                "❌ Administrator permissions required."
            )

            return

        new_value = (
            "false"
            if sticky_enabled(self.parent_view.config)
            else "true"
        )

        await self.parent_view.save(
            interaction,
            "sticky_enabled",
            new_value
        )


class StickyBackButton(discord.ui.Button):

    def __init__(self, parent):

        self.parent_view = parent

        super().__init__(
            label="Back to Setup",
            emoji="⬅️",
            style=discord.ButtonStyle.primary,
            row=3
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        view = SetupView(
            self.parent_view.guild,
            self.parent_view.config
        )

        await interaction.response.edit_message(
            embed=view.build_embed(),
            view=view
        )


class StickyView(discord.ui.View):
    """
    Sticky note settings: enable/disable, what to say,
    and which extra channel gets one.
    """

    def __init__(self, guild, config):

        super().__init__(
            timeout=300
        )

        self.guild = guild

        self.config = dict(
            config or {}
        )

        self.add_item(
            ConfigChannelSelect(
                self,
                "sticky_channel_id",
                "📌 Extra Sticky Channel (optional)",
                [discord.ChannelType.text]
            )
        )

        self.add_item(
            StickyToggleButton(self)
        )

        self.add_item(
            StickyTextButton(
                self,
                "buying_channel_id",
                "Buying Text",
                "🟢"
            )
        )

        self.add_item(
            StickyTextButton(
                self,
                "selling_channel_id",
                "Selling Text",
                "🔵"
            )
        )

        self.add_item(
            StickyTextButton(
                self,
                "sticky_channel_id",
                "Custom Text",
                "📌"
            )
        )

        self.add_item(
            StickyBackButton(self)
        )


    def build_embed(self):

        status = (
            "🟢 Enabled"
            if sticky_enabled(self.config)
            else "🔴 Disabled"
        )

        custom_ch = configured_channel(
            self.guild,
            self.config.get("sticky_channel_id")
        )

        def preview(key):

            text = sticky_text_for_key(
                self.config,
                key
            )

            text = text.replace("\n", " ")

            if len(text) > 90:
                text = text[:87] + "..."

            return text

        embed = discord.Embed(
            title="📌 Sticky Note Settings",
            description=(
                "A sticky note is re-posted at the "
                "bottom of the channel every time "
                "someone talks, so it always stays "
                "visible under the last ad.\n\n"

                f"**Status:** {status}\n"
                f"**Buying Channel:** "
                f"{configured_channel(self.guild, self.config.get('buying_channel_id'))}\n"
                f"**Selling Channel:** "
                f"{configured_channel(self.guild, self.config.get('selling_channel_id'))}\n"
                f"**Extra Sticky Channel:** {custom_ch}\n\n"

                f"🟢 **Buying text:** {preview('buying_channel_id')}\n"
                f"🔵 **Selling text:** {preview('selling_channel_id')}\n"
                f"📌 **Custom text:** {preview('sticky_channel_id')}"
            ),
            color=discord.Color.gold()
        )

        embed.set_footer(
            text=(
                "Only server administrators "
                "can configure the bot."
            )
        )

        return embed


    async def save(
        self,
        interaction,
        key,
        value
    ):

        try:

            await api.patch_config(
                interaction.guild.id,
                {
                    key: str(value)
                }
            )

        except Exception as e:

            print(
                f"[STICKY SETUP] {e}"
            )

            await safe_error(
                interaction,
                "❌ Couldn't save that setting to the backend."
            )

            return

        self.config[key] = str(value)

        invalidate_config_cache(interaction.guild.id)

        refreshed = StickyView(
            self.guild,
            self.config
        )

        try:

            await interaction.response.edit_message(
                embed=refreshed.build_embed(),
                view=refreshed
            )

        except Exception as e:

            print(
                f"[STICKY REFRESH] {e}"
            )

            await safe_error(
                interaction,
                "✅ Sticky settings updated."
            )


class StickySettingsButton(discord.ui.Button):

    def __init__(self, parent):

        self.parent_view = parent

        super().__init__(
            label="Sticky Notes",
            emoji="📌",
            style=discord.ButtonStyle.secondary,
            row=4
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.user.guild_permissions.administrator:

            await safe_error(
                interaction,
                "❌ Administrator permissions required."
            )

            return

        view = StickyView(
            self.parent_view.guild,
            self.parent_view.config
        )

        await interaction.response.edit_message(
            embed=view.build_embed(),
            view=view
        )


class LockedChannelSelect(discord.ui.ChannelSelect):

    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(
            placeholder="🔒 Locked Channels (auto-delete)",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=25,
            default_values=locked_channel_defaults(
                parent.guild,
                parent.config
            )
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Administrator permissions required.")
            return
        ids = ",".join(str(c.id) for c in self.values)
        await self.parent_view.save(interaction, "locked_channel_ids", ids)


class MMCategorySelect(discord.ui.ChannelSelect):

    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(
            placeholder="🎫 MM Ticket Category (auto-detect)",
            channel_types=[discord.ChannelType.category],
            min_values=0,
            max_values=1,
            row=1,
            default_values=channel_default(
                parent.guild,
                parent.config.get("mm_ticket_category_id")
            )
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Administrator permissions required.")
            return
        val = self.values[0].id if self.values else ""
        await self.parent_view.save(interaction, "mm_ticket_category_id", val)


class ChannelBackButton(discord.ui.Button):

    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(
            label="Back to Setup",
            emoji="⬅️",
            style=discord.ButtonStyle.primary,
            row=2
        )

    async def callback(self, interaction: discord.Interaction):
        view = SetupView(self.parent_view.guild, self.parent_view.config)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class MMAutoDetectToggle(discord.ui.Button):

    def __init__(self, parent):
        self.parent_view = parent
        on = str(
            (parent.config or {}).get("mm_autodetect", "true")
        ).strip().lower() in (
            "1",
            "true",
            "yes",
            "on"
        )
        super().__init__(
            label=(
                "MM Auto-Detect: ON"
                if on
                else "MM Auto-Detect: OFF"
            ),
            emoji="🤝",
            style=(
                discord.ButtonStyle.success
                if on
                else discord.ButtonStyle.danger
            ),
            row=3
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Administrator permissions required.")
            return
        on = str(
            (self.parent_view.config or {}).get("mm_autodetect", "true")
        ).strip().lower() in (
            "1",
            "true",
            "yes",
            "on"
        )
        await self.parent_view.save(
            interaction,
            "mm_autodetect",
            "false" if on else "true"
        )


class MMPrefixModal(discord.ui.Modal):

    def __init__(self, parent):
        super().__init__(title="MM Ticket Prefix")
        self.parent_view = parent
        self.prefix_input = discord.ui.TextInput(
            label="Ticket channel name prefix",
            default=str(
                (parent.config or {}).get("mm_ticket_prefix")
                or "need-middleman-"
            ),
            max_length=30,
            required=True
        )
        self.add_item(self.prefix_input)

    async def on_submit(self, interaction: discord.Interaction):
        val = self.prefix_input.value.strip()
        if not val:
            await interaction.response.send_message(
                "❌ Prefix can't be empty.",
                ephemeral=True
            )
            return
        await self.parent_view.save(interaction, "mm_ticket_prefix", val)


class MMPrefixButton(discord.ui.Button):

    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(
            label="MM Prefix",
            emoji="🏷️",
            style=discord.ButtonStyle.secondary,
            row=3
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Administrator permissions required.")
            return
        await interaction.response.send_modal(
            MMPrefixModal(self.parent_view)
        )


class ChannelSettingsView(discord.ui.View):
    """
    Extra channel settings that don't fit on the main
    setup panel (Discord's 5-row limit).
    """

    def __init__(self, guild, config):
        super().__init__(timeout=300)
        self.guild = guild
        self.config = dict(config or {})
        self.add_item(LockedChannelSelect(self))
        self.add_item(MMCategorySelect(self))
        self.add_item(ConfigChannelSelect(
            self,
            "stock_channel_id",
            "📦 Stock Post Channel (/stock post)",
            [discord.ChannelType.text],
            row=4
        ))
        self.add_item(ChannelBackButton(self))
        self.add_item(MMAutoDetectToggle(self))
        self.add_item(MMPrefixButton(self))

    def build_embed(self):
        return channel_settings_embed(self.guild, self.config)

    async def save(self, interaction, key, value):
        try:
            await api.patch_config(interaction.guild.id, {key: str(value)})
        except Exception as e:
            print(f"[CHANNEL SETUP] {e}")
            await safe_error(interaction, "❌ Couldn't save that setting to the backend.")
            return
        self.config[key] = str(value)
        invalidate_config_cache(interaction.guild.id)
        refreshed = ChannelSettingsView(self.guild, self.config)
        try:
            await interaction.response.edit_message(embed=refreshed.build_embed(), view=refreshed)
        except Exception as e:
            print(f"[CHANNEL REFRESH] {e}")
            await safe_error(interaction, "✅ Channel settings updated.")


class ChannelSettingsButton(discord.ui.Button):

    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(
            label="Channels",
            emoji="🔒",
            style=discord.ButtonStyle.secondary,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Administrator permissions required.")
            return
        view = ChannelSettingsView(self.parent_view.guild, self.parent_view.config)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class SetupView(discord.ui.View):

    def __init__(
        self,
        guild,
        config
    ):

        super().__init__(
            timeout=300
        )

        self.guild = guild

        # Existing saved settings, so nothing feels
        # like starting from scratch.
        self.config = dict(
            config or {}
        )

        self.add_item(
            ConfigChannelSelect(
                self,
                "buying_channel_id",
                "🟢 Buying Channel",
                [discord.ChannelType.text]
            )
        )

        self.add_item(
            ConfigChannelSelect(
                self,
                "selling_channel_id",
                "🔵 Selling Channel",
                [discord.ChannelType.text]
            )
        )

        self.add_item(
            ConfigChannelSelect(
                self,
                "ticket_category_id",
                "🎫 Ticket Category",
                [discord.ChannelType.category]
            )
        )

        self.add_item(
            ConfigChannelSelect(
                self,
                "mm_channel_id",
                "🤝 Middleman Channel",
                [discord.ChannelType.text]
            )
        )

        self.add_item(
            StickySettingsButton(self)
        )

        self.add_item(
            ChannelSettingsButton(self)
        )


    def build_embed(self):

        return setup_embed(
            self.guild,
            self.config
        )


    async def save(
        self,
        interaction,
        key,
        value
    ):

        try:

            await api.patch_config(
                interaction.guild.id,
                {
                    key: str(value)
                }
            )

        except Exception as e:

            print(
                f"[SETUP] {e}"
            )

            await safe_error(
                interaction,
                "❌ Couldn't save that setting to the backend."
            )

            return

        # Keep the local copy in sync and rebuild the panel
        # so the saved selections stay visible.

        self.config[key] = str(value)

        invalidate_config_cache(interaction.guild.id)

        refreshed = SetupView(
            self.guild,
            self.config
        )

        try:

            await interaction.response.edit_message(
                embed=refreshed.build_embed(),
                view=refreshed
            )

        except Exception as e:

            print(
                f"[SETUP REFRESH] {e}"
            )

            await safe_error(
                interaction,
                (
                    f"✅ **"
                    f"{key.replace('_', ' ').title()}"
                    f"** updated."
                )
            )


def setup_embed(guild, server_config):

    buying_ch = configured_channel(
        guild,
        server_config.get(
            "buying_channel_id"
        )
    )

    selling_ch = configured_channel(
        guild,
        server_config.get(
            "selling_channel_id"
        )
    )

    ticket_cat = configured_channel(
        guild,
        server_config.get(
            "ticket_category_id"
        )
    )

    mm_ch = configured_channel(
        guild,
        server_config.get(
            "mm_channel_id"
        )
    )

    stock_ch = configured_channel(
        guild,
        server_config.get(
            "stock_channel_id"
        )
    )

    locked_ch = locked_channel_mentions(
        guild,
        server_config
    )

    sticky_status = (
        "🟢 Enabled"
        if sticky_enabled(server_config)
        else "🔴 Disabled"
    )

    embed = discord.Embed(
        title="⚙️ SDBST Marketplace Setup",
        description=(
            "Here's your current configuration. "
            "Change anything you like below — "
            "everything else stays as it is.\n\n"

            f"🟢 **Buying Channel:** "
            f"{buying_ch}\n"

            f"🔵 **Selling Channel:** "
            f"{selling_ch}\n"

            f"🎫 **Ticket Category:** "
            f"{ticket_cat}\n"

            f"🤝 **MM Channel:** "
            f"{mm_ch}\n"

            f"📦 **Stock Post Channel:** "
            f"{stock_ch}\n"

            f"🔒 **Locked Channels:** "
            f"{locked_ch}\n"

            f"📌 **Sticky Notes:** "
            f"{sticky_status}"
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=(
            "Only server administrators "
            "can configure the bot."
        )
    )

    return embed


def channel_settings_embed(guild, server_config):

    locked_ch = locked_channel_mentions(guild, server_config)

    autodetect_on = str(
        (server_config or {}).get("mm_autodetect", "true")
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on"
    )

    prefix = str(
        (server_config or {}).get("mm_ticket_prefix")
        or "need-middleman-"
    )

    mm_cat = configured_channel(
        guild,
        (server_config or {}).get("mm_ticket_category_id")
    )

    embed = discord.Embed(
        title="🔒 Channel Settings",
        description=(
            "Extra channel configuration.\n\n"
            "🔒 **Locked Channels:** "
            f"{locked_ch}\n\n"
            "In locked channels the bot auto-deletes "
            "non-bot messages. Only `/mm` tickets "
            "(prefix match) are auto-locked; Tickety "
            "tickets stay chat-able.\n\n"
            f"🤝 **MM Auto-Detect:** {'🟢 ON' if autodetect_on else '🔴 OFF'}\n"
            f"🏷️ **MM Ticket Prefix:** {prefix}\n\n"
            f"🎫 **MM Ticket Category:** {mm_cat}\n"
            "When auto-detect is ON, the bot watches for "
            "ticket channels (created by Tickety or any "
            "ticket bot) in the MM Ticket Category, OR "
            "whose name starts with the prefix, and "
            "automatically starts the middleman deal "
            "flow inside them."
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=(
            "Only server administrators "
            "can configure the bot."
        )
    )

    return embed


# ============================================================
# /SETUP
# ============================================================

@bot.tree.command(
    name="setup",
    description="Configure SDBST Marketplace."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setup(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command must be used inside a server.",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    server_config = await get_server_config(
        interaction.guild.id
    )

    view = SetupView(
        interaction.guild,
        server_config
    )

    await interaction.followup.send(
        embed=view.build_embed(),
        view=view,
        ephemeral=True
    )


# ============================================================
# /LOCKCHECK (diagnostic)
# ============================================================

@bot.tree.command(
    name="lockcheck",
    description="Debug: check if this channel is locked."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def lockcheck(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ Use in a server.",
            ephemeral=True
        )

        return

    config = await get_server_config(
        interaction.guild.id
    )

    ids = locked_channel_ids(config)

    locked = is_locked_channel(
        interaction.channel,
        config
    )

    prefix = str(
        (config or {}).get("mm_ticket_prefix")
        or "need-middleman-"
    )

    raw_ids = (config or {}).get(
        "locked_channel_ids"
    )

    raw_single = (config or {}).get(
        "locked_channel_id"
    )

    autodetect = str(
        (config or {}).get("mm_autodetect", "true")
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on"
    )

    mm_cat_raw = (config or {}).get("mm_ticket_category_id")
    mm_cat_id = None
    try:
        mm_cat_id = int(mm_cat_raw) if mm_cat_raw else None
    except (TypeError, ValueError):
        mm_cat_id = None
    mm_cat_name = "❌ none configured"
    if mm_cat_id is not None:
        mm_cat_ch = interaction.guild.get_channel(mm_cat_id)
        if mm_cat_ch:
            mm_cat_name = f"{mm_cat_ch.name} (`{mm_cat_id}`)"
        else:
            mm_cat_name = f"⚠️ `{mm_cat_id}` (not found)"
    ch_cat_id = getattr(interaction.channel, "category_id", None)
    ch_cat_name = "None (no category)"
    if ch_cat_id:
        ch_cat_ch = interaction.guild.get_channel(ch_cat_id)
        if ch_cat_ch:
            ch_cat_name = f"{ch_cat_ch.name} (`{ch_cat_id}`)"
        else:
            ch_cat_name = f"`{ch_cat_id}`"
    cat_match = (mm_cat_id is not None and ch_cat_id == mm_cat_id)

    msg = (
        "**Lock Check**\n"
        f"Channel: {interaction.channel.mention} "
        f"(`{interaction.channel.id}`)\n"
        f"Channel name: `{interaction.channel.name}`\n"
        f"Is locked: "
        f"{'🟢 YES' if locked else '🔴 NO'}\n"
        f"Locked IDs in config: "
        f"{sorted(ids) if ids else 'none'}\n"
        f"raw locked_channel_ids: `{raw_ids}`\n"
        f"raw locked_channel_id: `{raw_single}`\n"
        f"MM prefix: `{prefix}`\n"
        f"MM auto-detect: "
        f"{'ON' if autodetect else 'OFF'}\n"
        f"Name starts with prefix: "
        f"{interaction.channel.name.startswith(prefix)}\n"
        f"Channel category: {ch_cat_name}\n"
        f"MM Ticket Category: {mm_cat_name}\n"
        f"Category match: {'🟢 YES' if cat_match else '🔴 NO'}"
    )

    await interaction.response.send_message(
        msg,
        ephemeral=True
    )


# ============================================================
# EDIT AD MODAL
# ============================================================

class EditAdModal(discord.ui.Modal):

    def __init__(self, ad):

        super().__init__(
            title="✏️ Edit Advertisement"
        )

        self.ad = ad

        self.item_input = discord.ui.TextInput(
            label="Item",
            default=str(
                ad.get("item", "")
            ),
            max_length=100,
            required=True
        )

        self.price_input = discord.ui.TextInput(
            label="Price (USD)",
            default=str(
                ad.get("price", "")
            ),
            max_length=20,
            required=True
        )

        self.add_item(
            self.item_input
        )

        self.add_item(
            self.price_input
        )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        try:

            price = float(
                self.price_input.value
            )

            if price <= 0:
                raise ValueError

        except ValueError:

            await interaction.response.send_message(
                (
                    "❌ Price must be a valid "
                    "number greater than $0."
                ),
                ephemeral=True
            )

            return

        item = self.item_input.value.strip()

        data = {
            "item": item,
            "price": str(price)
        }

        try:

            await api.update_ad(
                self.ad["ad_id"],
                data
            )

            channel = interaction.guild.get_channel(
                int(self.ad["channel_id"])
            )

            if channel:

                try:

                    message = await channel.fetch_message(
                        int(self.ad["message_id"])
                    )

                    updated_ad = dict(
                        self.ad
                    )

                    updated_ad.update(
                        data
                    )

                    await message.edit(
                        content=create_ad_text_from_data(
                            interaction.guild,
                            updated_ad
                        ),
                        embed=None,
                        view=AdButtons(
                            updated_ad
                        )
                    )

                except discord.NotFound:

                    pass

            await interaction.response.send_message(
                "✅ Advertisement updated.",
                ephemeral=True
            )

        except Exception as e:

            print(
                f"[EDIT] {e}"
            )

            await safe_error(
                interaction,
                "❌ Failed to update the advertisement."
            )


# ============================================================
# AD BUTTONS
# ============================================================

class AdButtons(discord.ui.View):

    def __init__(self, ad):

        super().__init__(
            timeout=None
        )

        self.ad = ad


    # ========================================================
    # OFFER
    # ========================================================

    @discord.ui.button(
        label="Offer",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="ad:offer"
    )
    async def offer(
        self,
        interaction,
        button
    ):

        guild = interaction.guild

        if guild is None:

            await safe_error(
                interaction,
                "❌ This can only be used inside a server."
            )

            return

        try:

            owner_id = int(
                self.ad["owner_id"]
            )

        except (TypeError, ValueError, KeyError):

            await safe_error(
                interaction,
                "❌ This advertisement has invalid owner data."
            )

            return

        if interaction.user.id == owner_id:

            await safe_error(
                interaction,
                "❌ You can't offer on your own ad."
            )

            return

        config = await get_server_config(
            guild.id
        )

        category_id = config.get(
            "ticket_category_id"
        )

        if not category_id:

            await safe_error(
                interaction,
                (
                    "❌ Tickets aren't configured. "
                    "Ask an administrator to run `/setup`."
                )
            )

            return

        try:

            category_id = int(
                category_id
            )

        except (TypeError, ValueError):

            await safe_error(
                interaction,
                "❌ Ticket category configuration is invalid."
            )

            return

        category = guild.get_channel(
            category_id
        )

        if not isinstance(
            category,
            discord.CategoryChannel
        ):

            await safe_error(
                interaction,
                (
                    "❌ The configured ticket "
                    "category doesn't exist."
                )
            )

            return

        try:

            seller = await guild.fetch_member(
                owner_id
            )

        except discord.NotFound:

            await safe_error(
                interaction,
                "❌ I couldn't find the ad owner."
            )

            return

        except discord.HTTPException as e:

            print(
                f"[FETCH SELLER] {e}"
            )

            await safe_error(
                interaction,
                "❌ Discord couldn't find the ad owner."
            )

            return

        # ----------------------------------------------------
        # Duplicate ticket check
        # ----------------------------------------------------

        try:

            tickets = await api.list_tickets(
                guild.id
            )

            for ticket in tickets:

                if ticket.get("status") != "open":
                    continue

                buyer = str(
                    ticket.get("buyer_id")
                )

                seller_db = str(
                    ticket.get("seller_id")
                )

                if (
                    buyer == str(interaction.user.id)
                    and seller_db == str(owner_id)
                ):

                    existing_channel_id = ticket.get(
                        "channel_id"
                    )

                    if existing_channel_id:

                        try:

                            existing = guild.get_channel(
                                int(existing_channel_id)
                            )

                        except (
                            TypeError,
                            ValueError
                        ):

                            existing = None

                        if existing:

                            await safe_error(
                                interaction,
                                (
                                    "❌ You already have "
                                    f"a ticket: {existing.mention}"
                                )
                            )

                            return

        except Exception as e:

            print(
                f"[TICKET CHECK] {e}"
            )

        # ----------------------------------------------------
        # Create Discord ticket
        # ----------------------------------------------------

        ticket_name = ticket_channel_name(
            interaction.user,
            seller
        )

        overwrites = {

            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            seller:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                ),

            interaction.user:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                ),

            guild.me:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True
                )
        }

        try:

            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"SDBST Trade • "
                    f"{self.ad.get('item')} • "
                    f"{money(self.ad.get('price'))}"
                )
            )

        except discord.Forbidden:

            await safe_error(
                interaction,
                (
                    "❌ I don't have permission "
                    "to create ticket channels."
                )
            )

            return

        except discord.HTTPException as e:

            print(
                f"[CHANNEL CREATE] {e}"
            )

            await safe_error(
                interaction,
                "❌ Discord failed to create the ticket."
            )

            return

        # ----------------------------------------------------
        # Save ticket to backend
        # ----------------------------------------------------

        ticket_data = {

            "server_id":
                str(guild.id),

            "ad_id":
                str(self.ad["ad_id"]),

            "channel_id":
                str(ticket_channel.id),

            "buyer_id":
                str(interaction.user.id),

            "seller_id":
                str(owner_id)
        }

        try:

            ticket_record = await api.create_ticket(
                ticket_data
            )

        except Exception as e:

            print(
                f"[TICKET API] {e}"
            )

            try:

                await ticket_channel.delete(
                    reason=(
                        "Backend ticket creation failed"
                    )
                )

            except Exception:
                pass

            await safe_error(
                interaction,
                (
                    "❌ The ticket couldn't be "
                    "saved to the backend. "
                    "Please try again."
                )
            )

            return

        # ----------------------------------------------------
        # Ticket opening message
        # ----------------------------------------------------

        ticket_message = None

        try:

            ticket_message = await ticket_channel.send(
                content=(
                    f"{seller.mention} "
                    f"{interaction.user.mention}\n\n"

                    f"🎫 **Trade ticket opened**\n"

                    f"**Item:** "
                    f"{self.ad.get('item')}\n"

                    f"**Price:** "
                    f"{money(self.ad.get('price'))}\n"

                    f"**Buyer:** "
                    f"{interaction.user.mention}\n"

                    f"**Seller:** "
                    f"{seller.mention}\n\n"

                    f"{NEGOTIATE_LINE}"
                ),
                view=TicketButtons(
                    ticket_record
                )
            )

        except Exception as e:

            print(
                f"[TICKET MESSAGE] {e}"
            )

        # A clean clickable link straight to the ticket.

        if ticket_message is not None:
            ticket_link = ticket_message.jump_url

        else:
            ticket_link = (
                f"https://discord.com/channels/"
                f"{guild.id}/{ticket_channel.id}"
            )

        await interaction.response.send_message(
            (
                f"[Click here to open a ticket ✔️]"
                f"({ticket_link})\n"
                f"💬 Negotiate the deal in the "
                f"opened ticket."
            ),
            ephemeral=True
        )


    # ========================================================
    # MARK DONE
    # ========================================================

    @discord.ui.button(
        label="Mark Done",
        emoji="✅",
        style=discord.ButtonStyle.secondary,
        custom_id="ad:done"
    )
    async def mark_done(
        self,
        interaction,
        button
    ):

        try:

            owner_id = int(
                self.ad["owner_id"]
            )

        except (TypeError, ValueError, KeyError):

            await safe_error(
                interaction,
                "❌ Invalid advertisement owner."
            )

            return

        if interaction.user.id != owner_id:

            await safe_error(
                interaction,
                (
                    "❌ Only the person who created "
                    "this ad can mark it as done."
                )
            )

            return

        await interaction.response.send_message(
            (
                "✅ Marking advertisement "
                "as completed..."
            ),
            ephemeral=True
        )

        try:

            await api.complete_ad(
                self.ad["ad_id"]
            )

        except Exception as e:

            print(
                f"[COMPLETE AD] {e}"
            )

        try:

            await interaction.message.delete()

        except Exception as e:

            print(
                f"[DELETE AD MESSAGE] {e}"
            )


    # ========================================================
    # EDIT
    # ========================================================

    @discord.ui.button(
        label="Edit Ad",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        custom_id="ad:edit"
    )
    async def edit_ad(
        self,
        interaction,
        button
    ):

        try:

            owner_id = int(
                self.ad["owner_id"]
            )

        except (TypeError, ValueError, KeyError):

            await safe_error(
                interaction,
                "❌ Invalid advertisement owner."
            )

            return

        if interaction.user.id != owner_id:

            await safe_error(
                interaction,
                (
                    "❌ Only the person who created "
                    "this ad can edit it."
                )
            )

            return

        await interaction.response.send_modal(
            EditAdModal(
                self.ad
            )
        )


# ============================================================
# TICKET BUTTONS
# ============================================================

class TicketButtons(discord.ui.View):

    def __init__(self, ticket):

        super().__init__(
            timeout=None
        )

        self.ticket = ticket


    # ========================================================
    # CLOSE
    # ========================================================

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close"
    )
    async def close_ticket(
        self,
        interaction,
        button
    ):

        try:

            buyer_id = int(
                self.ticket["buyer_id"]
            )

            seller_id = int(
                self.ticket["seller_id"]
            )

        except (TypeError, ValueError, KeyError):

            await safe_error(
                interaction,
                "❌ Invalid ticket data."
            )

            return

        if interaction.user.id not in {
            buyer_id,
            seller_id
        }:

            await safe_error(
                interaction,
                "❌ You aren't part of this ticket."
            )

            return

        await interaction.response.send_message("🔒 Closing ticket...", ephemeral=True)
        try:
            await api.close_ticket(self.ticket["ticket_id"])
        except Exception as e:
            print(f"[CLOSE TICKET API] {e}")
        guild = interaction.guild
        try:
            buyer = guild.get_member(buyer_id)
            seller = guild.get_member(seller_id)
            if buyer:
                await interaction.channel.set_permissions(buyer, view_channel=False, send_messages=False, read_message_history=False)
            if seller:
                await interaction.channel.set_permissions(seller, view_channel=False, send_messages=False, read_message_history=False)
        except Exception as e:
            print(f"[CLOSE TICKET PERMS] {e}")
        try:
            await interaction.message.edit(view=ClosedTicketView(self.ticket))
        except Exception as e:
            print(f"[CLOSE TICKET EDIT] {e}")
        try:
            await interaction.channel.send("🔒 This ticket has been closed. Staff can reopen or delete it.")
        except Exception:
            pass


    # ========================================================
    # REQUEST MM
    # ========================================================

    @discord.ui.button(
        label="Request MM",
        emoji="🤝",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:mm"
    )
    async def request_mm(
        self,
        interaction,
        button
    ):

        if interaction.guild is None:

            await safe_error(
                interaction,
                "❌ This can only be used inside a server."
            )

            return

        config = await get_server_config(
            interaction.guild.id
        )

        mm_channel_id = config.get(
            "mm_channel_id"
        )

        if not mm_channel_id:

            await safe_error(
                interaction,
                (
                    "❌ MM channel hasn't "
                    "been configured yet."
                )
            )

            return

        try:

            mm_channel = interaction.guild.get_channel(
                int(mm_channel_id)
            )

        except (TypeError, ValueError):

            mm_channel = None

        if not mm_channel:

            await safe_error(
                interaction,
                (
                    "❌ The configured MM channel "
                    "couldn't be found."
                )
            )

            return

        mm_link = (
            f"https://discord.com/channels/"
            f"{interaction.guild.id}/{mm_channel.id}"
        )

        await interaction.response.send_message(
            (
                f"🤝 **Middleman Request**\n"
                f"[Click here to open a ticket "
                f"and request MM]({mm_link}) "
                f"({mm_channel.mention})"
            ),
            ephemeral=True
        )


# ============================================================
# WTB / WTS MODAL
# ============================================================

class AdModal(discord.ui.Modal):

    def __init__(
        self,
        ad_type
    ):

        super().__init__(
            title=(
                "🟢 Want To Buy"
                if ad_type == "WTB"
                else
                "🔵 Want To Sell"
            )
        )

        self.ad_type = ad_type

        self.item_input = discord.ui.TextInput(
            label="Item",
            placeholder="Example: Inverted AWP",
            max_length=100,
            required=True
        )

        self.price_input = discord.ui.TextInput(
            label="Price (USD)",
            placeholder="Example: 105.00",
            max_length=20,
            required=True
        )

        self.add_item(
            self.item_input
        )

        self.add_item(
            self.price_input
        )


    async def on_submit(
        self,
        interaction
    ):

        try:

            price = float(
                self.price_input.value
            )

            if price <= 0:
                raise ValueError

        except ValueError:

            await interaction.response.send_message(
                (
                    "❌ Enter a valid price "
                    "greater than $0."
                ),
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        if interaction.guild is None:

            await interaction.followup.send(
                (
                    "❌ This command must be "
                    "used inside a server."
                ),
                ephemeral=True
            )

            return

        config = await get_server_config(
            interaction.guild.id
        )

        if self.ad_type == "WTB":

            channel_id = config.get(
                "buying_channel_id"
            )

        else:

            channel_id = config.get(
                "selling_channel_id"
            )

        if not channel_id:

            await interaction.followup.send(
                (
                    "❌ This marketplace channel "
                    "isn't configured.\n"
                    "Ask an administrator to "
                    "run `/setup`."
                ),
                ephemeral=True
            )

            return

        try:

            channel = interaction.guild.get_channel(
                int(channel_id)
            )

        except (TypeError, ValueError):

            channel = None

        if not channel:

            await interaction.followup.send(
                (
                    "❌ The configured marketplace "
                    "channel couldn't be found."
                ),
                ephemeral=True
            )

            return

        item = self.item_input.value.strip()

        await delete_duplicate_ads(interaction.guild, interaction.user.id, self.ad_type, item)

        content = create_ad_text(interaction, item, price, self.ad_type)

        # ----------------------------------------------------
        # Send message first so we get its Discord ID.
        # ----------------------------------------------------

        try:

            message = await channel.send(
                content=content
            )

        except discord.Forbidden:

            await interaction.followup.send(
                (
                    "❌ I don't have permission "
                    "to send messages in that channel."
                ),
                ephemeral=True
            )

            return

        except discord.HTTPException as e:

            print(
                f"[AD MESSAGE] {e}"
            )

            await interaction.followup.send(
                "❌ Discord failed to post the advertisement.",
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # Save ad to backend.
        # ----------------------------------------------------

        try:

            ad_record = await api.create_ad(
                {

                    "server_id":
                        str(interaction.guild.id),

                    "owner_id":
                        str(interaction.user.id),

                    "ad_type":
                        self.ad_type,

                    "item":
                        item,

                    "price":
                        str(price),

                    "message_id":
                        str(message.id),

                    "channel_id":
                        str(channel.id),

                }
            )

        except Exception as e:

            print(
                f"[CREATE AD API] {e}"
            )

            try:

                await message.delete()

            except Exception:
                pass

            await interaction.followup.send(
                (
                    "❌ The advertisement couldn't "
                    "be saved to the backend."
                ),
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # Attach persistent buttons.
        # ----------------------------------------------------

        try:

            await message.edit(
                view=AdButtons(
                    ad_record
                )
            )

        except Exception as e:

            print(
                f"[AD BUTTONS] {e}"
            )

        await interaction.followup.send(
            (
                f"✅ Your **{self.ad_type}** ad "
                f"was posted in {channel.mention}."
            ),
            ephemeral=True
        )


# ============================================================
# /WTB
# ============================================================

@bot.tree.command(
    name="wtb",
    description="Create a Want To Buy advertisement."
)
async def wtb(
    interaction: discord.Interaction
):

    await interaction.response.send_modal(
        AdModal("WTB")
    )


# ============================================================
# /WTS
# ============================================================

@bot.tree.command(
    name="wts",
    description="Create a Want To Sell advertisement."
)
async def wts(
    interaction: discord.Interaction
):

    await interaction.response.send_modal(
        AdModal("WTS")
    )


# ============================================================
# MIDDLEMAN (MM) FLOW
# ============================================================

class MMClaimButton(discord.ui.Button):

    def __init__(self, deal_id):
        super().__init__(
            label="Claim",
            emoji="🤝",
            style=discord.ButtonStyle.success,
            custom_id=f"mm:claim:{deal_id}"
        )
        self.deal_id = deal_id

    async def callback(self, interaction: discord.Interaction):
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await safe_error(interaction, "❌ This ticket is no longer active.")
            return
        if deal.get("claimed_by"):
            await safe_error(interaction, "❌ This ticket has already been claimed.")
            return
        deal["claimed_by"] = str(interaction.user.id)
        save_mm_deals(_mm_deals)
        embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds
            else discord.Embed(title="Ticket Created")
        )
        embed.description = (
            (embed.description or "")
            + f"\n\n🤝 **Claimed by {interaction.user.mention}**"
        )
        try:
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            print(f"[MM CLAIM EDIT] {e}")
        try:
            await interaction.channel.send(f"🤝 {interaction.user.mention} claimed this ticket.")
        except Exception:
            pass


class MMClaimView(discord.ui.View):

    def __init__(self, deal_id):
        super().__init__(timeout=None)
        self.deal_id = deal_id
        self.add_item(MMClaimButton(deal_id))


class MMUserSelect(discord.ui.UserSelect):

    def __init__(self, deal_id):
        super().__init__(
            placeholder="Select a user",
            min_values=1,
            max_values=1,
            custom_id=f"mm:select:{deal_id}"
        )
        self.deal_id = deal_id

    async def callback(self, interaction: discord.Interaction):
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await safe_error(interaction, "❌ This ticket is no longer active.")
            return
        if deal.get("creator_id") and str(interaction.user.id) != deal.get("creator_id"):
            await safe_error(interaction, "❌ Only the ticket creator can select the user they're dealing with.")
            return
        if not deal.get("creator_id"):
            deal["creator_id"] = str(interaction.user.id)
        selected = self.values[0]
        try:
            await interaction.channel.set_permissions(
                selected,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        except Exception as e:
            print(f"[MM ADD USER] {e}")
        deal["participants"] = [deal["creator_id"], str(selected.id)]
        deal["confirmed"] = {deal["creator_id"]: False, str(selected.id): False}
        deal["names"] = {
            deal["creator_id"]: (interaction.user.display_name or interaction.user.name),
            str(selected.id): (getattr(selected, "display_name", None) or getattr(selected, "name", None) or str(selected))
        }
        deal["state"] = "entering_deal"
        save_mm_deals(_mm_deals)
        try:
            await interaction.channel.send(f"✅ {selected.mention} has been added to the ticket.")
        except Exception:
            pass
        await interaction.response.send_modal(EnterDealModal(self.deal_id))


class MMSelectUserView(discord.ui.View):

    def __init__(self, deal_id):
        super().__init__(timeout=None)
        self.deal_id = deal_id
        self.add_item(MMUserSelect(deal_id))


class EnterDealModal(discord.ui.Modal):

    def __init__(self, deal_id, existing=None):
        super().__init__(title="Enter Deal")
        self.deal_id = deal_id
        existing = existing or {}
        self.item_input = discord.ui.TextInput(
            label="Item Name",
            placeholder="Item being traded",
            max_length=100,
            required=True,
            default=str(existing.get("item", ""))
        )
        self.price_input = discord.ui.TextInput(
            label="Price",
            placeholder="Example: 50.00",
            max_length=20,
            required=True,
            default=str(existing.get("price", ""))
        )
        self.payment_input = discord.ui.TextInput(
            label="Payment Method",
            placeholder="Example: PayPal, Crypto",
            max_length=50,
            required=True,
            default=str(existing.get("payment_method", ""))
        )
        self.add_item(self.item_input)
        self.add_item(self.price_input)
        self.add_item(self.payment_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = float(self.price_input.value)
            if price <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Price must be a valid number greater than $0.", ephemeral=True)
            return
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await interaction.response.send_message("❌ This ticket is no longer active.", ephemeral=True)
            return
        deal["item"] = self.item_input.value.strip()
        deal["price"] = str(price)
        deal["payment_method"] = self.payment_input.value.strip()
        deal["state"] = "confirming"
        save_mm_deals(_mm_deals)
        embed = mm_deal_embed(deal)
        view = DealConfirmView(self.deal_id)
        if deal.get("deal_message_id"):
            try:
                msg = await interaction.channel.fetch_message(int(deal["deal_message_id"]))
                await msg.edit(embed=embed, view=view)
            except Exception as e:
                print(f"[MM EDIT CARD] {e}")
            await interaction.response.send_message("✅ Deal updated.", ephemeral=True)
        else:
            try:
                msg = await interaction.channel.send(embed=embed, view=view)
                deal["deal_message_id"] = str(msg.id)
                save_mm_deals(_mm_deals)
                await interaction.response.send_message("✅ Deal saved.", ephemeral=True)
            except Exception as e:
                print(f"[MM SEND CARD] {e}")
                reason = str(e)
                # If embeds are blocked, try a plain-text deal card
                # so the trade can still proceed.
                try:
                    msg = await interaction.channel.send(
                        content=f"**{deal.get('item')} | {money(deal.get('price'))} | {deal.get('payment_method')}**\n"
                        + "\n".join(
                            f"{deal.get('names', {}).get(uid, f'<@{uid}>')}: "
                            + ("Confirmed ✅" if deal.get("confirmed", {}).get(uid) else "Unconfirmed ❌")
                            for uid in deal.get("participants", [])
                        ),
                        view=view
                    )
                    deal["deal_message_id"] = str(msg.id)
                    save_mm_deals(_mm_deals)
                    await safe_error(interaction, "✅ Deal saved (plain text).")
                except Exception as e2:
                    print(f"[MM SEND CARD FALLBACK] {e2}")
                    await safe_error(
                        interaction,
                        f"❌ Couldn't post the deal card: {reason}"
                    )


class MMConfirmButton(discord.ui.Button):

    def __init__(self, deal_id, user_id, label, style):
        super().__init__(
            label=label,
            style=style,
            custom_id=f"mm:confirm:{deal_id}:{user_id}",
            row=0
        )
        self.deal_id = deal_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await safe_error(interaction, "❌ This deal is no longer active.")
            return
        if str(interaction.user.id) != self.user_id:
            await safe_error(interaction, "❌ This isn't your confirm button.")
            return
        deal["confirmed"][self.user_id] = True
        save_mm_deals(_mm_deals)
        embed = mm_deal_embed(deal)
        view = DealConfirmView(self.deal_id)
        all_confirmed = all(deal["confirmed"].get(uid) for uid in deal.get("participants", []))
        if all_confirmed:
            for child in view.children:
                child.disabled = True
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"[MM CONFIRM EDIT] {e}")
        if all_confirmed:
            try:
                await interaction.followup.send("✅ All participants confirmed. A middleman may now proceed with this ticket.")
            except Exception:
                pass


class MMEditDealButton(discord.ui.Button):

    def __init__(self, deal_id):
        super().__init__(
            label="Edit Deal",
            emoji="✏️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"mm:edit:{deal_id}",
            row=1
        )
        self.deal_id = deal_id

    async def callback(self, interaction: discord.Interaction):
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await safe_error(interaction, "❌ This deal is no longer active.")
            return
        if str(interaction.user.id) != deal.get("creator_id"):
            await safe_error(interaction, "❌ Only the deal creator can edit the deal.")
            return
        await interaction.response.send_modal(EnterDealModal(self.deal_id, existing=deal))


class MMCloseButton(discord.ui.Button):

    def __init__(self, deal_id):
        super().__init__(
            label="Close Ticket",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"mm:close:{deal_id}",
            row=1
        )
        self.deal_id = deal_id

    async def callback(self, interaction: discord.Interaction):
        deal = _mm_deals.get(self.deal_id)
        if not deal:
            await safe_error(interaction, "❌ This ticket is no longer active.")
            return
        if str(interaction.user.id) != deal.get("creator_id"):
            await safe_error(interaction, "❌ Only the ticket creator can close this ticket.")
            return
        await interaction.response.send_message("🔒 Closing ticket...", ephemeral=True)
        _mm_deals.pop(self.deal_id, None)
        save_mm_deals(_mm_deals)
        try:
            await interaction.channel.delete(reason=f"MM ticket closed by {interaction.user}")
        except Exception as e:
            print(f"[MM CLOSE] {e}")


class DealConfirmView(discord.ui.View):

    def __init__(self, deal_id):
        super().__init__(timeout=None)
        self.deal_id = deal_id
        deal = _mm_deals.get(deal_id, {})
        participants = deal.get("participants", [])
        confirmed = deal.get("confirmed", {})
        names = deal.get("names", {})
        for uid in participants:
            name = names.get(uid, uid)
            is_confirmed = confirmed.get(uid, False)
            label = f"{name} Confirmed" if is_confirmed else f"{name} Confirm"
            style = discord.ButtonStyle.primary if is_confirmed else discord.ButtonStyle.success
            self.add_item(MMConfirmButton(deal_id, uid, label, style))
        self.add_item(MMEditDealButton(deal_id))
        self.add_item(MMCloseButton(deal_id))
        if participants and all(confirmed.get(uid) for uid in participants):
            for child in self.children:
                child.disabled = True


# ============================================================
# /MM
# ============================================================

@bot.tree.command(
    name="mm",
    description="Request a middleman — opens an MM ticket."
)
async def mm(interaction: discord.Interaction):
    if interaction.guild is None:
        await safe_error(interaction, "❌ This command must be used inside a server.")
        return
    config = await get_server_config(interaction.guild.id)
    category_id = config.get("ticket_category_id")
    if not category_id:
        await safe_error(interaction, "❌ Tickets aren't configured. Ask an admin to run `/setup`.")
        return
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        await safe_error(interaction, "❌ Ticket category configuration is invalid.")
        return
    category = interaction.guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        await safe_error(interaction, "❌ The configured ticket category doesn't exist.")
        return
    await interaction.response.defer(ephemeral=True)
    ticket_num = random.randint(1000, 9999)
    channel_name = f"need-middleman-{ticket_num}"
    _pending_mm_channels.add(
        (interaction.guild.id, channel_name)
    )
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    }
    try:
        ticket_channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"SDBST Middleman Ticket • {interaction.user}"
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to create ticket channels.", ephemeral=True)
        return
    except discord.HTTPException as e:
        print(f"[MM CHANNEL] {e}")
        await interaction.followup.send("❌ Discord failed to create the ticket.", ephemeral=True)
        return
    deal_id = uuid.uuid4().hex[:8]
    claim_embed = discord.Embed(
        title="Ticket Created",
        description=(
            f"{interaction.user.mention}\n\n"
            "Automatically state the user and deal in the ticket\n"
            "To ensure ur safety\n"
            "All activities within your MM tickets will be recorded including edited messages and deleted images"
        ),
        color=discord.Color.blue()
    )
    claim_embed.set_footer(text="SDBST Support | SDBST")
    try:
        claim_msg = await ticket_channel.send(embed=claim_embed, view=MMClaimView(deal_id))
    except Exception as e:
        print(f"[MM CLAIM MSG] {e}")
        claim_msg = None
    select_embed = discord.Embed(
        description=(
            "**Who are you dealing with?**\n"
            "Please select from the dropdown, ping them, or type their user ID."
        ),
        color=discord.Color.blue()
    )
    try:
        select_msg = await ticket_channel.send(embed=select_embed, view=MMSelectUserView(deal_id))
    except Exception as e:
        print(f"[MM SELECT MSG] {e}")
        select_msg = None
    _mm_deals[deal_id] = {
        "guild_id": str(interaction.guild.id),
        "ticket_channel_id": str(ticket_channel.id),
        "creator_id": str(interaction.user.id),
        "participants": [],
        "confirmed": {},
        "names": {},
        "item": None,
        "price": None,
        "payment_method": None,
        "claimed_by": None,
        "claim_message_id": str(claim_msg.id) if claim_msg else None,
        "select_message_id": str(select_msg.id) if select_msg else None,
        "deal_message_id": None,
        "state": "awaiting_user"
    }
    save_mm_deals(_mm_deals)
    if claim_msg and select_msg:
        await interaction.followup.send(f"🤝 Middleman ticket opened: {ticket_channel.mention}", ephemeral=True)
    else:
        await interaction.followup.send("⚠️ Ticket created, but some setup messages failed to send.", ephemeral=True)


# ============================================================
# RESTORE MM VIEWS
# ============================================================

async def restore_mm_views():
    count = 0
    for deal_id, deal in list(_mm_deals.items()):
        guild = None
        try:
            guild = bot.get_guild(int(deal["guild_id"]))
        except Exception:
            guild = None
        if guild is None:
            continue
        if deal.get("claim_message_id") and not deal.get("claimed_by"):
            try:
                bot.add_view(MMClaimView(deal_id), message_id=int(deal["claim_message_id"]))
                count += 1
            except Exception as e:
                print(f"[RESTORE MM CLAIM] {e}")
        if deal.get("select_message_id") and deal.get("state") == "awaiting_user":
            try:
                bot.add_view(MMSelectUserView(deal_id), message_id=int(deal["select_message_id"]))
                count += 1
            except Exception as e:
                print(f"[RESTORE MM SELECT] {e}")
        if deal.get("deal_message_id") and deal.get("state") == "confirming":
            try:
                bot.add_view(DealConfirmView(deal_id), message_id=int(deal["deal_message_id"]))
                count += 1
            except Exception as e:
                print(f"[RESTORE MM DEAL] {e}")
    print(f"[RESTORE MM] Restored {count} MM view(s).")


# ============================================================
# RESTORE PERSISTENT VIEWS
# ============================================================

async def restore_persistent_views():

    total_ads = 0

    total_tickets = 0

    # Restore views in EVERY guild the bot is in,
    # not just the test server.

    for guild in bot.guilds:

        print(
            f"[RESTORE] Checking "
            f"{guild.name} ({guild.id})"
        )

        # --------------------------------------------------------
        # Restore advertisements
        # --------------------------------------------------------

        try:

            ads = await api.list_ads(
                guild.id,
                limit=100
            )


            for ad in ads:

                if ad.get("status") == "completed":
                    continue

                channel_id = ad.get(
                    "channel_id"
                )

                message_id = ad.get(
                    "message_id"
                )

                if not channel_id or not message_id:
                    continue

                try:

                    channel = guild.get_channel(
                        int(channel_id)
                    )

                    if channel is None:
                        continue

                    message = await channel.fetch_message(
                        int(message_id)
                    )

                    bot.add_view(
                        AdButtons(ad),
                        message_id=message.id
                    )

                    total_ads += 1

                except discord.NotFound:

                    print(
                        f"[RESTORE AD] Message "
                        f"{message_id} no longer exists."
                    )

                except Exception as e:

                    print(
                        f"[RESTORE AD] {e}"
                    )


        except Exception as e:

            print(
                f"[RESTORE ADS] {e}"
            )


        # --------------------------------------------------------
        # Restore tickets
        # --------------------------------------------------------

        try:

            tickets = await api.list_tickets(
                guild.id
            )


            for ticket in tickets:
                channel_id = ticket.get("channel_id")
                ticket_id = ticket.get("ticket_id")
                if not channel_id or not ticket_id:
                    continue
                try:
                    channel = guild.get_channel(int(channel_id))
                except (TypeError, ValueError):
                    channel = None
                if channel is None:
                    continue
                if ticket.get("status") == "closed":
                    view_cls = ClosedTicketView
                else:
                    view_cls = TicketButtons
                try:
                    async for message in channel.history(limit=20):
                        if message.author.id != bot.user.id:
                            continue
                        bot.add_view(view_cls(ticket), message_id=message.id)
                        total_tickets += 1
                        break
                except Exception as e:
                    print(f"[RESTORE TICKET] {e}")

        except Exception as e:

            print(
                f"[RESTORE TICKETS] {e}"
            )

    print(
        f"[RESTORE] Restored {total_ads} ads "
        f"and {total_tickets} tickets."
    )


# ============================================================
# STICKY NOTES (buying / selling / custom channel)
# ============================================================

# guild_id -> (config, timestamp)
_config_cache = {}

# channel_id -> sticky message id
_sticky_messages = {}

# channel_id -> last repost timestamp
_sticky_last = {}

# channels currently reposting their sticky
_sticky_posting = set()

# channels we've already warned about missing
# Manage Messages permission (locked channel)
_locked_warned = set()


def invalidate_config_cache(guild_id):

    _config_cache.pop(
        int(guild_id),
        None
    )


async def cached_config(guild_id):

    now = time.time()

    cached = _config_cache.get(guild_id)

    if cached and now - cached[1] < 60:
        return cached[0]

    config = await get_server_config(guild_id)

    _config_cache[guild_id] = (config, now)

    return config


async def refresh_sticky(channel, text):

    now = time.time()

    if now - _sticky_last.get(channel.id, 0) < 5:
        return

    if channel.id in _sticky_posting:
        return

    _sticky_last[channel.id] = now
    _sticky_posting.add(channel.id)

    try:

        # Delete any existing sticky messages (tracked or
        # left over from a previous run) so only one stays
        # at the bottom of the channel.
        try:

            async for msg in channel.history(limit=20):

                if msg.author.id != bot.user.id:
                    continue

                is_sticky = (
                    msg.id == _sticky_messages.get(channel.id)
                    or (msg.content or "").startswith("📌")
                )

                if is_sticky:
                    try:
                        await msg.delete()
                    except Exception:
                        pass

        except Exception as e:

            print(f"[STICKY CLEANUP] {e}")

        try:

            sent = await channel.send(text)

            _sticky_messages[channel.id] = sent.id

        except Exception as e:

            print(
                f"[STICKY] {e}"
            )

    finally:

        _sticky_posting.discard(channel.id)


@bot.event
async def on_guild_channel_create(channel):
    """Auto-start the MM deal flow when a ticket bot
    (Tickety) creates a middleman ticket channel."""

    name = getattr(channel, "name", "") or ""

    print(
        f"[CHANNEL CREATED] #{name} "
        f"(id {getattr(channel, 'id', '?')}) "
        f"in guild "
        f"{getattr(channel.guild, 'id', '?')}"
    )

    if not name or channel.guild is None:
        return

    config = await cached_config_safe(
        channel.guild.id
    )

    if not str(
        (config or {}).get("mm_autodetect", "true")
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on"
    ):
        print("[MM AUTODETECT] OFF — skipping")
        return

    mm_cat = (config or {}).get("mm_ticket_category_id")
    try:
        mm_cat_id = int(mm_cat) if mm_cat else None
    except (TypeError, ValueError):
        mm_cat_id = None

    prefix = str(
        (config or {}).get("mm_ticket_prefix")
        or "need-middleman-"
    )

    cat_match = (
        mm_cat_id is not None
        and getattr(channel, "category_id", None) == mm_cat_id
    )
    prefix_match = name.startswith(prefix)

    if not cat_match and not prefix_match:
        print(
            f"[MM AUTODETECT] #{name} does NOT match "
            f"category {mm_cat_id} or prefix '{prefix}' "
            f"(channel category: "
            f"{getattr(channel, 'category_id', None)}) "
            f"— skipping"
        )
        return

    print(
        f"[MM AUTODETECT] #{name} matched "
        f"({'category' if cat_match else 'prefix'}) "
        f"— starting deal flow"
    )

    # Skip channels the bot itself is creating via /mm
    # (registered before creation) to avoid double
    # posting the deal flow.
    key = (channel.guild.id, name)

    if key in _pending_mm_channels:
        _pending_mm_channels.discard(key)
        return

    # Give the ticket bot a moment to finish setting up
    # the ticket (overwrites, opener, welcome message)
    # before we post the deal flow.
    await asyncio.sleep(2)

    # Tickety may not grant our bot access to the ticket
    # channel by default, so make sure we can see and
    # post in it before trying to send the deal flow.
    try:
        await channel.set_permissions(
            channel.guild.me,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True
        )
    except Exception as e:
        print(f"[MM AUTODETECT PERMS] {e}")

    opener = _find_ticket_opener(channel)

    deal_id = uuid.uuid4().hex[:8]

    select_embed = discord.Embed(
        description=(
            "**Who are you dealing with?**\n"
            "Please select from the dropdown, "
            "ping them, or type their user ID."
        ),
        color=discord.Color.blue()
    )

    try:

        select_msg = await channel.send(
            embed=select_embed,
            view=MMSelectUserView(deal_id)
        )

    except Exception as e:

        print(f"[MM AUTODETECT SEND] {e}")

        return

    _mm_deals[deal_id] = {
        "guild_id": str(channel.guild.id),
        "ticket_channel_id": str(channel.id),
        "creator_id": (
            str(opener.id) if opener else None
        ),
        "participants": [],
        "confirmed": {},
        "names": {},
        "item": None,
        "price": None,
        "payment_method": None,
        "claimed_by": None,
        "claim_message_id": None,
        "select_message_id": str(select_msg.id),
        "deal_message_id": None,
        "state": "awaiting_user"
    }

    save_mm_deals(_mm_deals)

    print(
        f"[MM AUTODETECT] Started deal {deal_id} "
        f"in #{name}"
    )


@bot.event
async def on_message(message):

    if message.guild is None:

        await bot.process_commands(message)

        return

    config = await cached_config_safe(
        message.guild.id
    )

    # ----------------------------------------------------
    # Locked channel: auto-delete anything
    # that isn't the bot's own message.
    # ----------------------------------------------------

    is_locked = (
        not message.author.bot
        and is_locked_channel(
            message.channel,
            config
        )
    )

    if is_locked:

        print(
            f"[LOCKED] msg from {message.author} "
            f"in #{message.channel.name} "
            f"(id {message.channel.id}) — deleting"
        )

        try:

            await message.delete()

        except discord.Forbidden:

            print(
                f"[LOCKED DELETE] Missing Manage Messages "
                f"permission in #{message.channel}."
            )

            if message.channel.id not in _locked_warned:

                _locked_warned.add(message.channel.id)

                try:

                    await message.channel.send(
                        "⚠️ I need **Manage Messages** "
                        "permission to lock this channel. "
                        "Give the bot role Manage Messages, "
                        "then messages here will be "
                        "auto-deleted."
                    )

                except Exception:

                    pass

        except Exception as e:

            print(
                f"[LOCKED DELETE] {e}"
            )

        return

    # Never react to our own sticky note, otherwise
    # the bot would repost itself forever.
    if message.id in _sticky_messages.values():

        await bot.process_commands(message)

        return

    if message.channel.id in _sticky_posting:

        await bot.process_commands(message)

        return

    try:

        text = sticky_text_for_channel(
            config,
            message.channel.id
        )

        if text:

            await refresh_sticky(
                message.channel,
                text
            )

    except Exception as e:

        print(
            f"[STICKY CHECK] {e}"
        )

    await bot.process_commands(message)


# ============================================================
# COMMAND ERROR HANDLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await safe_error(
            interaction,
            (
                "❌ You need administrator "
                "permissions to use this command."
            )
        )

        return

    print(
        f"[COMMAND ERROR] {repr(error)}"
    )

    await safe_error(
        interaction,
        (
            "❌ Something went wrong "
            "while running that command."
        )
    )


# ============================================================
# RUN
# ============================================================

# Startup is intentionally placed at the end of the file so every cog,
# persistent view, and restoration function is defined before on_ready.

# ============================================================
# (merged from bot_extras.py)
# ============================================================

# bot_extras.py — stock posts, closed-ticket views, and
# duplicate-ad cleanup. Loaded as a cog by bot.py.
# Imports from bot.py lazily at runtime, so it MUST be
# imported after bot.py is fully loaded (done in setup_hook).

import json
import uuid
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# STOCK POSTS (local JSON persistence)
# ============================================================

STOCK_POSTS_FILE = Path("stock_posts.json")

def load_stock_posts():
    if STOCK_POSTS_FILE.exists():
        try:
            return json.loads(STOCK_POSTS_FILE.read_text())
        except Exception as e:
            print(f"[STOCK POSTS LOAD] {e}")
    return {}

def save_stock_posts(data):
    try:
        STOCK_POSTS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[STOCK POSTS SAVE] {e}")

_stock_posts = load_stock_posts()


# ============================================================
# TRADE TICKET HELPER
# ============================================================

async def create_trade_ticket(guild, buyer, seller, item, price, ad_id):
    """Create a trade ticket between a buyer and seller.
    Returns {ok, channel, record, error}."""
    config = await get_server_config(guild.id)
    category_id = config.get("ticket_category_id")
    if not category_id:
        return {"ok": False, "error": "❌ Tickets aren't configured. Ask an administrator to run `/setup`."}
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "❌ Ticket category configuration is invalid."}
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return {"ok": False, "error": "❌ The configured ticket category doesn't exist."}
    try:
        tickets = await api.list_tickets(guild.id)
        for ticket in tickets:
            if ticket.get("status") != "open":
                continue
            if str(ticket.get("buyer_id")) == str(buyer.id) and str(ticket.get("seller_id")) == str(seller.id):
                ex_id = ticket.get("channel_id")
                if ex_id:
                    try:
                        existing = guild.get_channel(int(ex_id))
                    except (TypeError, ValueError):
                        existing = None
                    if existing:
                        return {"ok": False, "error": f"❌ You already have a ticket: {existing.mention}"}
    except Exception as e:
        print(f"[TICKET CHECK] {e}")
    ticket_name = ticket_channel_name(buyer, seller)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        seller: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        buyer: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
    }
    try:
        ticket_channel = await guild.create_text_channel(
            name=ticket_name, category=category, overwrites=overwrites,
            topic=f"SDBST Trade • {item} • {money(price)}"
        )
    except discord.Forbidden:
        return {"ok": False, "error": "❌ I don't have permission to create ticket channels."}
    except discord.HTTPException as e:
        print(f"[CHANNEL CREATE] {e}")
        return {"ok": False, "error": "❌ Discord failed to create the ticket."}
    ticket_data = {
        "server_id": str(guild.id), "ad_id": str(ad_id),
        "channel_id": str(ticket_channel.id),
        "buyer_id": str(buyer.id), "seller_id": str(seller.id),
    }
    try:
        ticket_record = await api.create_ticket(ticket_data)
    except Exception as e:
        print(f"[TICKET API] {e}")
        try:
            await ticket_channel.delete(reason="Backend ticket creation failed")
        except Exception:
            pass
        return {"ok": False, "error": "❌ The ticket couldn't be saved to the backend. Please try again."}
    return {"ok": True, "channel": ticket_channel, "record": ticket_record}


# ============================================================
# STOCK BUTTONS
# ============================================================

class StockBuyButton(discord.ui.Button):
    def __init__(self, post_id):
        super().__init__(label="Buy This Item", emoji="🛒", style=discord.ButtonStyle.secondary, custom_id=f"stock:buy:{post_id}")
        self.post_id = post_id

    async def callback(self, interaction: discord.Interaction):
        post = _stock_posts.get(self.post_id)
        if not post:
            await safe_error(interaction, "❌ This stock item is no longer available.")
            return
        guild = interaction.guild
        if guild is None:
            await safe_error(interaction, "❌ This can only be used inside a server.")
            return
        try:
            seller = await guild.fetch_member(int(post["poster_id"]))
        except Exception as e:
            print(f"[STOCK BUY SELLER] {e}")
            await safe_error(interaction, "❌ Couldn't find the seller.")
            return
        if interaction.user.id == seller.id:
            await safe_error(interaction, "❌ You can't buy your own stock item.")
            return
        result = await create_trade_ticket(guild, interaction.user, seller, post.get("name"), post.get("price"), f"stock-{self.post_id}")
        if not result["ok"]:
            await safe_error(interaction, result["error"])
            return
        ticket_channel = result["channel"]
        ticket_record = result["record"]
        ticket_message = None
        try:
            ticket_message = await ticket_channel.send(
                content=(
                    f"{seller.mention} {interaction.user.mention}\n\n"
                    f"🎫 **Trade ticket opened**\n"
                    f"**Item:** {post.get('name')}\n"
                    f"**Price:** {money(post.get('price'))}\n"
                    f"**Buyer:** {interaction.user.mention}\n"
                    f"**Seller:** {seller.mention}\n\n"
                    f"{NEGOTIATE_LINE}"
                ),
                view=TicketButtons(ticket_record)
            )
        except Exception as e:
            print(f"[STOCK TICKET MSG] {e}")
        if ticket_message is not None:
            ticket_link = ticket_message.jump_url
        else:
            ticket_link = f"https://discord.com/channels/{guild.id}/{ticket_channel.id}"
        await interaction.response.send_message(
            f"[Click here to open a ticket ✔️]({ticket_link})\n💬 Negotiate the deal in the opened ticket.",
            ephemeral=True
        )


class StockSoldButton(discord.ui.Button):
    def __init__(self, post_id):
        super().__init__(label="Sold", emoji="✅", style=discord.ButtonStyle.danger, custom_id=f"stock:sold:{post_id}")
        self.post_id = post_id

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Only staff can mark items as sold.")
            return
        await interaction.response.send_message("✅ Marking as sold...", ephemeral=True)
        _stock_posts.pop(self.post_id, None)
        save_stock_posts(_stock_posts)
        try:
            await interaction.message.delete()
        except Exception as e:
            print(f"[STOCK SOLD DELETE] {e}")


class StockPostButtons(discord.ui.View):
    def __init__(self, post_id):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.add_item(StockBuyButton(post_id))
        self.add_item(StockSoldButton(post_id))


# ============================================================
# CLOSED TICKET VIEW (reopen / delete — staff only)
# ============================================================

class ReopenTicketButton(discord.ui.Button):
    def __init__(self, ticket):
        super().__init__(label="Reopen Ticket", emoji="🔓", style=discord.ButtonStyle.success, custom_id="ticket:reopen")
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Only staff can reopen tickets.")
            return
        guild = interaction.guild
        try:
            buyer_id = int(self.ticket["buyer_id"])
            seller_id = int(self.ticket["seller_id"])
        except (TypeError, ValueError, KeyError):
            await safe_error(interaction, "❌ Invalid ticket data.")
            return
        try:
            buyer = guild.get_member(buyer_id)
            seller = guild.get_member(seller_id)
            if buyer:
                await interaction.channel.set_permissions(buyer, view_channel=True, send_messages=True, read_message_history=True)
            if seller:
                await interaction.channel.set_permissions(seller, view_channel=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print(f"[REOPEN PERMS] {e}")
        try:
            await api.update_ticket(self.ticket["ticket_id"], {"status": "open"})
        except Exception as e:
            print(f"[REOPEN TICKET API] {e}")
        try:
            await interaction.response.edit_message(view=TicketButtons(self.ticket))
        except Exception as e:
            print(f"[REOPEN EDIT] {e}")
        try:
            await interaction.channel.send("🔓 Ticket reopened by staff.")
        except Exception:
            pass


class DeleteTicketButton(discord.ui.Button):
    def __init__(self, ticket):
        super().__init__(label="Delete Ticket", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ticket:delete")
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await safe_error(interaction, "❌ Only staff can delete tickets.")
            return
        await interaction.response.send_message("🗑️ Deleting ticket...", ephemeral=True)
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except Exception as e:
            print(f"[DELETE TICKET] {e}")


class ClosedTicketView(discord.ui.View):
    def __init__(self, ticket):
        super().__init__(timeout=None)
        self.ticket = ticket
        self.add_item(ReopenTicketButton(ticket))
        self.add_item(DeleteTicketButton(ticket))


# ============================================================
# DUPLICATE AD CLEANUP
# ============================================================

async def delete_duplicate_ads(guild, user_id, ad_type, item):
    try:
        existing_ads = await api.list_ads(guild.id, limit=100)
        for ad in existing_ads:
            if ad.get("status") == "completed":
                continue
            if str(ad.get("owner_id")) != str(user_id):
                continue
            if ad.get("ad_type") != ad_type:
                continue
            if str(ad.get("item", "")).strip().lower() != item.lower():
                continue
            try:
                old_ch = guild.get_channel(int(ad.get("channel_id")))
                if old_ch:
                    old_msg = await old_ch.fetch_message(int(ad.get("message_id")))
                    await old_msg.delete()
            except Exception as e:
                print(f"[DUPE AD MSG] {e}")
            try:
                await api.delete_ad(ad["ad_id"])
            except Exception as e:
                print(f"[DUPE AD API] {e}")
    except Exception as e:
        print(f"[DUPE CHECK] {e}")


# ============================================================
# RESTORE STOCK VIEWS
# ============================================================

async def restore_stock_views():
    count = 0
    for post_id, post in list(_stock_posts.items()):
        try:
            bot.add_view(StockPostButtons(post_id), message_id=int(post["message_id"]))
            count += 1
        except Exception as e:
            print(f"[RESTORE STOCK] {e}")
    print(f"[RESTORE STOCK] Restored {count} stock view(s).")


# ============================================================
# STOCK COG (/stock post — staff only)
# ============================================================

class StockCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    stock = app_commands.Group(name="stock", description="Stock management commands.")

    @stock.command(name="post", description="Post a stock item (staff only).")
    @app_commands.checks.has_permissions(administrator=True)
    async def post(
        self,
        interaction: discord.Interaction,
        name: str,
        price: str,
        channel: discord.TextChannel = None,
        image_url: str = None,
        image: discord.Attachment = None,
    ):
        if interaction.guild is None:
            await safe_error(interaction, "❌ Use in a server.")
            return
        try:
            price_val = float(price)
            if price_val <= 0:
                raise ValueError
        except ValueError:
            await safe_error(interaction, "❌ Price must be a valid number greater than $0.")
            return
        config = await get_server_config(interaction.guild.id)
        configured_stock_channel = config.get("stock_channel_id")
        if channel is not None:
            stock_channel = channel
        elif configured_stock_channel:
            try:
                stock_channel = interaction.guild.get_channel(int(configured_stock_channel))
            except (TypeError, ValueError):
                stock_channel = None
            if not isinstance(stock_channel, discord.TextChannel):
                await safe_error(
                    interaction,
                    "❌ The configured stock channel is invalid or no longer exists. "
                    "Update it under `/setup` → **Channels**."
                )
                return
        else:
            stock_channel = interaction.channel
        await interaction.response.defer(ephemeral=True)
        img = image_url or None
        file_to_send = None
        if not img and image:
            try:
                file_to_send = await image.to_file()
            except Exception as e:
                print(f"[STOCK IMAGE] {e}")
        if price_val == int(price_val):
            price_str = f"${int(price_val):,}"
        else:
            price_str = f"${price_val:,.2f}"
        now = datetime.now()
        now_str = f"{now.month}/{now.day}/{str(now.year)[2:]}, {now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"
        embed = discord.Embed(title=name.upper(), color=discord.Color.blurple())
        embed.add_field(name="💰 Price", value=price_str, inline=True)
        embed.set_footer(text=f"SD Gems — Stock | {now_str}")
        if img:
            embed.set_thumbnail(url=img)
        post_id = uuid.uuid4().hex[:8]
        view = StockPostButtons(post_id)
        try:
            msg = await stock_channel.send(embed=embed, file=file_to_send, view=view)
        except Exception as e:
            print(f"[STOCK POST] {e}")
            await interaction.followup.send("❌ Couldn't post the stock item.", ephemeral=True)
            return
        if file_to_send and msg.attachments:
            img = msg.attachments[0].url
            embed.set_thumbnail(url=img)
            try:
                await msg.edit(embed=embed)
            except Exception as e:
                print(f"[STOCK THUMB] {e}")
        _stock_posts[post_id] = {
            "guild_id": str(interaction.guild.id),
            "channel_id": str(stock_channel.id),
            "message_id": str(msg.id),
            "poster_id": str(interaction.user.id),
            "name": name,
            "price": str(price_val),
            "image": img or None,
        }
        save_stock_posts(_stock_posts)
        await interaction.followup.send(f"✅ Stock item posted in {stock_channel.mention}.", ephemeral=True)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    print("============================================")
    print("SDBST Marketplace Bot starting...")
    print("============================================")
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("Bot stopped.")
    except Exception as e:
        print(f"[FATAL] {repr(e)}")
    finally:
        print("Bot shutdown complete.")

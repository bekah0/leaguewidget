import os
import json
import sqlite3
from urllib.parse import quote

import aiohttp
import discord
from datetime import datetime, timezone, timedelta
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DEFAULT_PLATFORM = os.getenv("DEFAULT_PLATFORM", "kr").lower()

DB_PATH = "riot_widget.db"


PLATFORM_HOSTS = {
    "kr": "kr.api.riotgames.com",
    "jp1": "jp1.api.riotgames.com",
    "na1": "na1.api.riotgames.com",
    "euw1": "euw1.api.riotgames.com",
    "eun1": "eun1.api.riotgames.com",
    "br1": "br1.api.riotgames.com",
    "la1": "la1.api.riotgames.com",
    "la2": "la2.api.riotgames.com",
    "oc1": "oc1.api.riotgames.com",
    "tr1": "tr1.api.riotgames.com",
    "ru": "ru.api.riotgames.com",
    "ph2": "ph2.api.riotgames.com",
    "sg2": "sg2.api.riotgames.com",
    "th2": "th2.api.riotgames.com",
    "tw2": "tw2.api.riotgames.com",
    "vn2": "vn2.api.riotgames.com",
}


ACCOUNT_CLUSTER = {
    "kr": "asia",
    "jp1": "asia",
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "oc1": "asia",
    "ph2": "asia",
    "sg2": "asia",
    "th2": "asia",
    "tw2": "asia",
    "vn2": "asia",
}


class RiotApiError(Exception):
    pass


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                game_name TEXT NOT NULL,
                tag_line TEXT NOT NULL,
                platform TEXT NOT NULL
            )
            """
        )


def save_user(discord_id: int, game_name: str, tag_line: str, platform: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (discord_id, game_name, tag_line, platform)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id)
            DO UPDATE SET
                game_name = excluded.game_name,
                tag_line = excluded.tag_line,
                platform = excluded.platform
            """,
            (str(discord_id), game_name, tag_line, platform),
        )


def get_user(discord_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT game_name, tag_line, platform FROM users WHERE discord_id = ?",
            (str(discord_id),),
        )
        return cur.fetchone()


class RiotClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.headers = {"X-Riot-Token": RIOT_API_KEY}

    async def get_json(self, url: str):
        async with self.session.get(url, headers=self.headers) as resp:
            text = await resp.text()

            if resp.status == 404:
                return None

            if resp.status == 429:
                raise RiotApiError("Riot API rate limit")

            if resp.status < 200 or resp.status >= 300:
                raise RiotApiError(f"Riot API 오류 {resp.status}: {text}")

            return json.loads(text)

    async def get_account_by_riot_id(self, game_name: str, tag_line: str, platform: str):
        cluster = ACCOUNT_CLUSTER.get(platform)
        if not cluster:
            raise RiotApiError(f"지원하지 않는 platform 값: {platform}")

        encoded_game_name = quote(game_name, safe="")
        encoded_tag_line = quote(tag_line, safe="")

        url = (
            f"https://{cluster}.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{encoded_game_name}/{encoded_tag_line}"
        )
        return await self.get_json(url)

    async def get_summoner_by_puuid(self, puuid: str, platform: str):
        host = PLATFORM_HOSTS.get(platform)
        if not host:
            raise RiotApiError(f"지원하지 않는 platform 값: {platform}")

        url = f"https://{host}/lol/summoner/v4/summoners/by-puuid/{puuid}"
        return await self.get_json(url)

    async def get_top_masteries(self, puuid: str, platform: str, count: int = 2):
        host = PLATFORM_HOSTS.get(platform)
        if not host:
            raise RiotApiError(f"지원하지 않는 platform 값: {platform}")

        url = (
            f"https://{host}"
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top"
            f"?count={count}"
        )
        data = await self.get_json(url)
        return data or []

    async def get_ddragon_version(self):
        url = "https://ddragon.leagueoflegends.com/api/versions.json"
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            versions = await resp.json()
            return versions[0]

    async def get_champion_map(self, version: str):
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/champion.json"
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()

        result = {}

        for champion in data["data"].values():
            champion_id_number = int(champion["key"])
            result[champion_id_number] = {
                "id": champion["id"],
                "name": champion["name"],
                "icon": champion["image"]["full"],
            }

        return result
    
    async def get_recent_match_ids(self, puuid: str, platform: str, count: int = 1):
        cluster = ACCOUNT_CLUSTER.get(platform)
        if not cluster:
            raise RiotApiError(f"지원하지 않는 platform 값: {platform}")

        url = (
            f"https://{cluster}.api.riotgames.com"
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
            f"?start=0&count={count}"
        )
        data = await self.get_json(url)
        return data or []

    async def get_match(self, match_id: str, platform: str):
        cluster = ACCOUNT_CLUSTER.get(platform)
        if not cluster:
            raise RiotApiError(f"지원하지 않는 platform 값: {platform}")

        url = f"https://{cluster}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        return await self.get_json(url)

    async def get_latest_game_date_text(self, puuid: str, platform: str):
        match_ids = await self.get_recent_match_ids(
            puuid=puuid,
            platform=platform,
            count=1,
        )

        if not match_ids:
            return "0/0/0"

        match = await self.get_match(match_ids[0], platform)
        if not match:
            return "0/0/0"

        game_creation_ms = match.get("info", {}).get("gameCreation")

        if not game_creation_ms:
            return "0/0/0"

        kst = timezone(timedelta(hours=9))

        played_at = datetime.fromtimestamp(
            game_creation_ms / 1000,
            tz=timezone.utc,
        ).astimezone(kst)

        return played_at.strftime("%Y/%m/%d")




def mastery_text(mastery: dict | None):
    if not mastery:
        return "Mastery 0", "0"

    level = mastery.get("championLevel", 0)
    points = mastery.get("championPoints", 0)

    return f"Mastery [ {level} ]", f"> {points:,}"


def champion_icon_url(version: str, champion: dict | None):
    if not champion:
        return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/Garen.png"

    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion['icon']}"

def profile_icon_url(version: str, profile_icon_id: int):
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{profile_icon_id}.png"


def champion_splash_url(champion: dict | None):
    if not champion:
        return "https://ddragon.leagueoflegends.com/cdn/img/champion/splash/Garen_0.jpg"

    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion['id']}_0.jpg"


async def build_widget_payload(
    session: aiohttp.ClientSession,
    game_name: str,
    tag_line: str,
    platform: str,
):
    riot = RiotClient(session)

    account = await riot.get_account_by_riot_id(game_name, tag_line, platform)
    if not account:
        raise RiotApiError(f"Riot ID를 찾지 못했다: {game_name}#{tag_line}")

    puuid = account["puuid"]

    summoner = await riot.get_summoner_by_puuid(puuid, platform)
    if not summoner:
        raise RiotApiError("소환사 정보를 찾지 못했다")

    masteries = await riot.get_top_masteries(puuid, platform, count=2)

    version = await riot.get_ddragon_version()
    champion_map = await riot.get_champion_map(version)

    mastery_1 = masteries[0] if len(masteries) >= 1 else None
    mastery_2 = masteries[1] if len(masteries) >= 2 else None

    champ_1 = champion_map.get(mastery_1["championId"]) if mastery_1 else None
    champ_2 = champion_map.get(mastery_2["championId"]) if mastery_2 else None

    mastery_1_title, mastery_1_points = mastery_text(mastery_1)
    mastery_2_title, mastery_2_points = mastery_text(mastery_2)

    riot_id = f"{game_name}"
    riot_tag = f"{tag_line}"
    level = summoner.get("summonerLevel", 1)

    profile_icon_id = summoner.get("profileIconId", 0)

    latest_game_date = await riot.get_latest_game_date_text(
    puuid=puuid,
    platform=platform,
    )

    payload = {
        "username": riot_id,
        "data": {
            "dynamic": [
                {
                    "type": 3,
                    "name": "hero_image",
                    "value": {
                        "url": "https://ddragon.leagueoflegends.com/cdn/img/champion/tiles/Lucian_72.jpg"
                    },
                },
                {
                    "type": 1,
                    "name": "riot_id",
                    "value": riot_id,
                },
                {
                    "type": 1,
                    "name": "status_text",
                    "value": f"# {riot_tag}",
                },
                {
                    "type": 1,
                    "name": "level_text",
                    "value": f"Lv. {level}",
                },
                {
                    "type": 1,
                    "name": "hours_spent",
                    "value": latest_game_date,
                },
                {
                    "type": 1,
                    "name": "mastery_1_title",
                    "value": mastery_1_title,
                },
                {
                    "type": 1,
                    "name": "mastery_2_title",
                    "value": mastery_2_title,
                },
                {
                    "type": 1,
                    "name": "mastery_1_points",
                    "value": mastery_1_points,
                },
                {
                    "type": 1,
                    "name": "mastery_2_points",
                    "value": mastery_2_points,
                },
                {
                    "type": 3,
                    "name": "mastery_1_icon",
                    "value": {
                        "url": champion_icon_url(version, champ_1)
                    },
                },
                {
                    "type": 3,
                    "name": "mastery_2_icon",
                    "value": {
                        "url": champion_icon_url(version, champ_2)
                    },
                },
                {
                    "type": 3,
                    "name": "user_icon",
                    "value": {
                        "url": profile_icon_url(version, profile_icon_id)
                    },
                },
            ]
        },
    }

    return payload


async def patch_discord_widget(
    session: aiohttp.ClientSession,
    discord_id: int,
    payload: dict,
):
    url = (
        f"https://discord.com/api/v9/applications/{DISCORD_APPLICATION_ID}"
        f"/users/{discord_id}/identities/0/profile"
    )

    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json",
    }

    async with session.patch(url, headers=headers, json=payload) as resp:
        text = await resp.text()

        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"Discord PATCH 실패 {resp.status}: {text}")

        return text


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

riot_group = app_commands.Group(
    name="riot",
    description="Riot API 기반 Discord 프로필 위젯 갱신"
)


@riot_group.command(name="setup", description="Riot ID 저장, 위젯 갱신")
@app_commands.describe(
    game_name="Riot ID 앞부분. 예:my nickname",
    tag_line="태그. 예: KR1",
    platform="서버 플랫폼. 한국은 kr",
)
async def riot_setup(
    interaction: discord.Interaction,
    game_name: str,
    tag_line: str,
    platform: str = DEFAULT_PLATFORM,
):
    await interaction.response.defer(ephemeral=True)

    platform = platform.lower().strip()
    save_user(interaction.user.id, game_name.strip(), tag_line.strip(), platform)

    try:
        async with aiohttp.ClientSession() as session:
            payload = await build_widget_payload(
                session=session,
                game_name=game_name.strip(),
                tag_line=tag_line.strip(),
                platform=platform,
            )

            await patch_discord_widget(
                session=session,
                discord_id=interaction.user.id,
                payload=payload,
            )

    except Exception as e:
        await interaction.followup.send(
            f"저장은 됐지만 위젯 갱신에 실패\n```text\n{e}\n```",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"`{game_name}#{tag_line}` 기준으로 위젯 갱신",
        ephemeral=True,
    )


@riot_group.command(name="refresh", description="저장된 Riot ID로 위젯 다시 갱신")
async def riot_refresh(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    row = get_user(interaction.user.id)
    if not row:
        await interaction.followup.send(
            "저장된 Riot ID 없음",
            ephemeral=True,
        )
        return

    game_name, tag_line, platform = row

    try:
        async with aiohttp.ClientSession() as session:
            payload = await build_widget_payload(
                session=session,
                game_name=game_name,
                tag_line=tag_line,
                platform=platform,
            )

            await patch_discord_widget(
                session=session,
                discord_id=interaction.user.id,
                payload=payload,
            )

    except Exception as e:
        await interaction.followup.send(
            f"위젯 갱신 실패\n```text\n{e}\n```",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"`{game_name}#{tag_line}` 위젯 다시 갱신",
        ephemeral=True,
    )


@bot.event
async def on_ready():
    init_db()

    try:
        bot.tree.add_command(riot_group)
    except app_commands.CommandAlreadyRegistered:
        pass

    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


if not DISCORD_TOKEN:
    raise RuntimeError("NO DISCORD_TOKEN")

if not DISCORD_APPLICATION_ID:
    raise RuntimeError("NO DISCORD_APPLICATION_ID")

if not RIOT_API_KEY:
    raise RuntimeError("NO RIOT_API_KEY")

bot.run(DISCORD_TOKEN)
import re

import discord
from discord import Interaction
from discord import app_commands
from dotenv import load_dotenv
from discord import ui

import os
import requests
import time, atexit

intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

class JibbleLogin(ui.Modal, title='Jibble Login'):
    email = ui.TextInput(label='Email', placeholder='Enter your email address')
    password = ui.TextInput(label='Password [! NOT HIDDEN !])', placeholder='Enter your password (NO HIDDEN SUPPORT)')

    async def get_access_token(self):
        token_url = "https://identity.prod.jibble.io/connect/token"

        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        payload = {
            'client_id': 'ro.client',
            'grant_type': 'password',
            'username': self.email,
            'password': self.password,
        }

        response = requests.request("POST", token_url, headers=headers, data=payload)

        if response.status_code == 200:
            data = response.json()
            access_token = data.get('access_token')
            return access_token
        else:
            raise Exception(f"Failed to get access token: {response.status_code} - {response.text}")

    @staticmethod
    async def get_id(access_token):
        id_url = "https://identity.prod.jibble.io/v1/AuthenticatablePeople"
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}',
        }
        response = requests.request("GET", id_url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            person_id = data.get('value')[0].get('id')
            return person_id
        else:
            raise Exception(f"Failed to get person ID: {response.status_code} - {response.text}")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            access_token = await self.get_access_token()
            person_id = await self.get_id(access_token)

            if access_token and person_id:
                JIBBLE_PERSONS_LIST[interaction.user.id] = person_id

                await interaction.followup.send(
                    f'Success association made!\n\n'
                    f'Discord User - **{interaction.user.display_name}** **[ {interaction.user.name} ]**\n'
                    f'Jibble Person ID - **{person_id}**',
                    ephemeral=True
                )

            else:
                print(f"Access Token: {access_token}, Person ID: {person_id}")
                await interaction.followup.send('Failed to retrieve access token or person ID.', ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.followup.send(f'An error occurred: {error}', ephemeral=True)


class JibbleTimeTracking:
    def __init__(self, person_id):
        self.time_tracking_url = "https://time-tracking.prod.jibble.io/v1/TimeEntries"
        self.access_token = None
        self.person_id = person_id

    async def get_access_token(self):
        try:
            token_url = "https://identity.prod.jibble.io/connect/token"

            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            payload = {
                'grant_type': 'client_credentials',
                'client_id': os.getenv('JIBBLE_CLIENT_ID'),
                'client_secret': os.getenv('JIBBLE_CLIENT_SECRET'),
            }

            response = requests.post(token_url, headers=headers, data=payload)

            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get('access_token')
                if not self.access_token:
                    raise Exception("Access token not found in response.")

        except Exception as e:
            print(f"Error getting access token: {e}")
            raise e

    async def clock(self, clock_type):
        try:
            if not self.access_token:
                await self.get_access_token()

            headers = {
                'Accept': 'application/json',
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }

            payload = {
                "personId": self.person_id,
                "type": clock_type,
                "clientType": "Web",
                "platform": {},
            }

            response = requests.post(self.time_tracking_url, headers=headers, json=payload)

            if response.status_code == 201:
                return response.json()
            else:
                raise Exception(f"Failed to clock in: {response.status_code} - {response.text}")

        except Exception as e:
            print(f"Error clocking in: {e}")
            raise e


async def clock(interaction: Interaction, clock_type: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    person_id = JIBBLE_PERSONS_LIST.get(interaction.user.id)

    if not person_id:
        await interaction.followup.send(
            "You need to connect your Jibble account first using `/jibble_connect`.", ephemeral=True)
        return

    try:
        jibble_tracker = JibbleTimeTracking(person_id)
        response = await jibble_tracker.clock(clock_type)
        await interaction.followup.send(
            f"Clocked {clock_type.lower()} successfully! [ **{response.get('time')}** ]", ephemeral=True)
        await availablity_rename(interaction, "Available" if clock_type == "In" else "Unavailable")

    except Exception as e:
        await interaction.followup.send(f"Error clocking {clock_type.lower()}: {e}", ephemeral=True)


async def availablity_rename(interaction: Interaction, status: str):
    user = client.get_user(interaction.user.id)

    if not user:
        await interaction.followup.send(
            "I can't find your user information. Please try again later.", ephemeral=True)
        return

    if not user.mutual_guilds:
        await interaction.followup.send(
            "I can't find any mutual guilds to change your nickname.", ephemeral=True)
        return

    message = ""
    for guild in user.mutual_guilds:
        try:
            member = guild.get_member(user.id)
            original_name = re.sub(r'\s*\[.*?]', '', member.display_name)

            await guild.get_member(user.id).edit(nick=f"{original_name} [{status}]")
            message += f"Nickname changed to **{interaction.user.display_name} [{status}]** in {guild.name}.\n"
            return
        except discord.Forbidden:
            message += f"I don't have permission to change your nickname in {guild.name}. (Possibly because you're the server owner) Please rename yourself manually.\n"
            continue
        except discord.HTTPException as e:
            message += f"Failed to change nickname in {guild.name}: {e}\n"
            continue
        except Exception as e:
            message += f"An unexpected error occurred in {guild.name}: {e}\n"
            continue
    if message:
        await interaction.followup.send(message, ephemeral=True)

@tree.command(name="jibble_connect", description="Connect to your Jibble account")
async def jibble_login(interaction: Interaction):
    await interaction.response.send_modal(JibbleLogin())


@tree.command(name="in", description="Clock in to Jibble")
async def clock_in(interaction: Interaction):
    await clock(interaction, "In")


@tree.command(name="out", description="Clock out of Jibble")
async def clock_out(interaction: Interaction):
    await clock(interaction, "Out")

@tree.command(name="brb", description="Set your status to temporarily unavailable")
async def brb(interaction: Interaction, reason: str = "Taking a break"):
    await interaction.response.defer(thinking=True, ephemeral=True)
    await availablity_rename(interaction, "BRB")
    global UNAVAILABLE_USERS_LIST
    UNAVAILABLE_USERS_LIST += {interaction.user.id: reason}


@tree.command(name="ping", description="Ping the bot")
async def ping(interaction: Interaction):
    await interaction.response.send_message(f'Pong! Latency: {round(client.latency * 1000)}ms', ephemeral=True)


@tree.command(name="echo", description="Echo a message")
async def echo(interaction: Interaction, message: str, message2: str, message3: str = "default"):
    await interaction.response.send_message(f"You said: {message}, {message2}, {message3}")

@client.event
async def on_message(message):
    global UNAVAILABLE_USERS_LIST
    if message.raw_mentions in UNAVAILABLE_USERS_LIST:
        reason = UNAVAILABLE_USERS_LIST[message.raw_mentions]
        await message.channel.send(f"Hello {[user.display_name for user in message.mentions]}, "
                                   f"is/are currently unavailable. Reason: {reason}")


@client.event
async def on_ready():
    print(f'We have logged in as {client.user}')
    await tree.sync()
    print("Synced the command tree.")


if __name__ == "__main__":
    JIBBLE_PERSONS_LIST = {}
    UNAVAILABLE_USERS_LIST = []

    # Register a function to run on exit
    def exit_handler():
        print("Bot is shutting down...")
        with open('jibble_persons_list.txt', 'w') as f:
            for USER_ID, PERSON_ID in JIBBLE_PERSONS_LIST.items():
                f.write(f"{USER_ID}:{PERSON_ID}\n")

    atexit.register(exit_handler)
    print("Bot is starting...")

    load_dotenv()

    try:
        with open('jibble_persons_list.txt', 'r') as f:
            for line in f:
                USER_ID, PERSON_ID = line.strip().split(':')
                JIBBLE_PERSONS_LIST[int(USER_ID)] = PERSON_ID

    except FileNotFoundError:
        print("No previous Jibble persons list found, starting fresh.")

    print("Loaded Jibble persons list:", JIBBLE_PERSONS_LIST)

    token = os.getenv('BOT_DISCORD_TOKEN')
    print("Token:", token)
    client.run(token)
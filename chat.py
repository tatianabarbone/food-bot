import discord, asyncio, aiohttp
from discord.ext.tasks import loop

import random
import json

import torch

from model import NeuralNet
from nltk_utils import bag_of_words, tokenize


## Run the Model ##
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

with open('intents.json', 'r') as json_data:
    intents = json.load(json_data)

FILE = "data.pth"
data = torch.load(FILE)

input_size = data["input_size"]
hidden_size = data["hidden_size"]
output_size = data["output_size"]
all_words = data['all_words']
tags = data['tags']
model_state = data["model_state"]

model = NeuralNet(input_size, hidden_size, output_size).to(device)
model.load_state_dict(model_state)
model.eval()

## Discord Functions ##
BOT_TOKEN = open('bot_token.txt', 'r').readline()
SPOONACULAR_KEY = open('spoonacular_key.txt', 'r').readline()
TATIANA_DISCORD_INFO = open('tatiana_discord_info.txt', 'r').readlines()

client = discord.Client() #start the client

@client.event
async def on_ready(): # called when client is done preparing data
    print(f"We have logged in successfully as {client.user}")

state = {} #maintain user state
endpoint = "https://api.spoonacular.com/recipes/"

meal_types = ['main course', 'side dish', 'dessert', 'appetizer', 'salad',
                        'bread', 'breakfast', 'soup', 'beverage', 'sauce', 'marinade','fingerfood', 
                        'snack', 'drink']

meal_emoji = ['🍽️', '🍲', '🍰', '🥘', '🥗', '🍞', '🍳', '🍜', '☕️',
    '🥫', '🥩', '🍿', '🍪', '🍸']


@client.event
async def on_message(message): # called when a message is sent and received
    print(f"{message.channel}: {message.author}: {message.author.name}: {message.content}")
    
    # if the message came from the bot, don't reply
    if message.author == client.user:
        return

    tag_probability = get_tag_probability(message)
    tag = tag_probability[0] # the predicted tag
    prob = tag_probability[1] # the certainty

    if prob.item() > 0.75:
        for intent in intents['intents']:
            if tag == intent["tag"]:
                # Simple response
                if tag in ('greeting','goodbye','thanks'):
                    response = random.choice(intent['responses'])
                    await message.channel.send(response)
                elif tag == 'joke': # Spoonacular API
                    joke = await get_joke()
                    await message.channel.send(joke['text'])
                elif tag == 'cook': # Spoonacular API
                    
                    if str(message.author) != client.user:
                        state[message.author.id] = 'wants-to-cook'

                        await message.channel.send("Do you have any specific ingredient(s) in mind?")
                        
                elif state[message.author.id] == 'wants-to-cook' and tag == 'affirm':
                    state[message.author.id] = 'yes-ingredients'
                    await message.channel.send("Please enter the ingredients you would like to use, comma-separated.")

                elif state[message.author.id] == 'yes-ingredients':
                    ingredients = message.content
                    results = await get_recipes_by_ingredients(ingredients)

                    if len(results) == 0:
                        await message.channel.send("I'm sorry, I didn't find any recipes with those ingredients.")

                    client.loop.create_task(send_embed(results, message, False, True)) #True for a request by_ingredients

                elif state[message.author.id] == 'wants-to-cook' and tag == 'deny':
                    state[message.author.id] = 'no-ingredients'
                    if str(message.author) != client.user:
                        await message.channel.send("What type of meal are you looking for?\n Here are the options:\n" + stringify(meal_types, meal_emoji))
                
                elif state[message.author.id] == 'wants-to-cook' and tag == 'uncertain': # Send 5 random recipes
        
                    results = await get_random_recipes()
                    print(results)
                    client.loop.create_task(send_embed(results, message, True, False)) #True for random recipe

                elif tag == 'utter-meal-type': # complex search
                    print('Making request for recipe...')

                    results = await complex_search(message.content.lower(), message)
                    print(results)

                    if len(results) == 0:
                        await message.channel.send("I'm sorry, I didn't find any recipes that match your search.")

                    await message.channel.send("React :thumbsup: to any recipe to get a similar recipe") 

                    # Send multiple embeds
                    client.loop.create_task(send_embed(results, message, False, False))

                    
    else:
        await message.channel.send("I'm sorry, I didn't get that.")

def check(reaction, user): 
    return user != 'tatiana-bot#9022' and str(reaction.emoji) == '👍'

@client.event
async def on_error(event, *args, **kwargs):
    message = args[0] # Gets the message object
    await message.channel.send("You caused an error.") 


async def make_request(url, params):
    """
    Makes http request with url and params.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            print(resp.status)
            return await resp.json()
            
async def construct_embed(message, res_id):
    """Constructs and sends an embed to the channel
    given the result id"""
    
    recipe_info = await get_recipe_info(res_id)

    recipe_summary = await get_recipe_summary(res_id)
    summary = recipe_summary["summary"].replace("<b>", "").replace("</b>", "")

    ingredients = await get_ingredients_by_id(res_id)

    ing_list = []
    for ingredient in ingredients["ingredients"]:
        ing_list.append(ingredient["name"])

    ingredients = ', '.join([i for i in ing_list])

    sourceName = recipe_info["sourceName"]
    sourceUrl = recipe_info["sourceUrl"]
    title = recipe_info["title"]
    food_image_url = recipe_info["image"]
    readyInMinutes = recipe_info["readyInMinutes"]
    

    embed = discord.Embed(title=title, description=summary[:253] + "...", color=random.randint(0, 0xffffff), url=sourceUrl) 
    embed.set_image(url=food_image_url)
    embed.add_field(name="Ready in Minutes", value=str(readyInMinutes))
    embed.add_field(name="Ingredients", value=ingredients)
    embed.set_footer(text=str(res_id))
            
    await message.channel.send(embed=embed)


async def send_embed(results, message, random_recipe, by_ingredients):
    """Creates and sends an embed for each recipe in results dict, extracting info from the json appropriately depending on 
    if the random recipe or by ingredients endpoint is called"""
    if by_ingredients:
        for recipe in results:
            await construct_embed(message, recipe["id"])
    elif random_recipe:
        for recipe in results['recipes']:
            await construct_embed(message, recipe["id"])
    else:
        for result in results['results']: 
            print(result)
            await construct_embed(message, result["id"])

    # Wait 60sec for a thumbs up react for any of the messages. Get the id of the message reacted to, then request for similar.
    try:
        reaction, user = await client.wait_for('reaction_add', timeout=60.0, check=check)
    except asyncio.TimeoutError:
        pass
    else:
        reaction_id = int(reaction.message.embeds[0].footer.text)
        sim_rec = await get_similar_recipe(reaction_id)
        sim_rec_id = sim_rec[0]["id"]

        await message.channel.send("Here's a similar recipe to " + reaction.message.embeds[0].title + ":")
        await construct_embed(message, sim_rec_id)

    
def get_tag_probability(message):
    sentence = tokenize(message.content)
    X = bag_of_words(sentence, all_words)
    X = X.reshape(1, X.shape[0])
    X = torch.from_numpy(X).to(device)

    output = model(X)
    _, predicted = torch.max(output, dim=1)

    tag = tags[predicted.item()]

    probs = torch.softmax(output, dim=1)
    prob = probs[0][predicted.item()]
    return (tag, prob)

def stringify(my_list, emoji_list):
    """Concatenates items from two lists at the same index"""
    s = ""
    for i, item in enumerate(my_list,1):
        s += "\t" + emoji_list[my_list.index(item)] + "  " + item + "\n"
    return s

async def get_joke():
    """Gets a random food joke"""
    url = 'https://api.spoonacular.com/food/jokes/random'
    params = {'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def get_random_recipes():
    """Gets 5 random recipes"""
    url = endpoint + 'random'
    params = {
        'number': 10,
        'apiKey': SPOONACULAR_KEY}

    return await make_request(url, params)
    
async def get_recipes_by_ingredients(ingredients):
    """Gets recipes by ingredients"""
    url = endpoint + 'findByIngredients'
    params = {
        'ingredients': ingredients,
        'number': 10,
        'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def get_ingredients_by_id(res_id):
    """Gets ingredients of the recipe by id"""
    url = endpoint + f"{res_id}/ingredientWidget.json"
    params = {'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def get_recipe_info(res_id):
    """Gets info about a recipe given the id"""
    url = endpoint + f"{res_id}/information"
    params = {'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def get_recipe_summary(res_id):
    """Gets a short summary of the recipe given the id"""
    url = endpoint + f"{res_id}/summary"
    params = {'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def get_similar_recipe(recipe_id):
    """Gets a similar recipe to that of recipe_id"""
    url = endpoint + f"{recipe_id}/similar"
    params = {
        'number': 1,
        'apiKey': SPOONACULAR_KEY}
    return await make_request(url, params)

async def complex_search(meal_type, message):
    """Gets 5 recipes based on user preferences"""
    url = endpoint + "complexSearch"

    if str(message.author) == TATIANA_DISCORD_INFO[0].strip("\n"):
        DISCORD_INFO = TATIANA_DISCORD_INFO
    
    params = {
        'query': 'healthy',
        'excludeCuisine': DISCORD_INFO[2],
        'intolerances': DISCORD_INFO[3],
        'excludeIngredients': DISCORD_INFO[4],
        'type': meal_type,
        'maxSugar': DISCORD_INFO[6],
        'number': 10,
        'apiKey': SPOONACULAR_KEY
    }
    return await make_request(url, params)

client.run(BOT_TOKEN) #run the client

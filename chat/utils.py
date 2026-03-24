import random


ADJECTIVES = [
    "Mighty", "Golden", "Silent", "Brave", "Swift", "Calm", "Bold", "Keen",
    "Radiant", "Steady", "Wise", "Vibrant"
]

KENYAN_NATURE_NOUNS = [
    "Savannah", "Rhino", "Baobab", "Acacia", "Flamingo", "Mara", "Kifaru",
    "Oryx", "Tusker", "Rift", "Tsavo", "Tana"
]


def generate_random_alias():
    return f"{random.choice(ADJECTIVES)} {random.choice(KENYAN_NATURE_NOUNS)}"

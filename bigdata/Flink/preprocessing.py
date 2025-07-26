import re
import string
import html
import contractions
import nltk
from textblob import TextBlob
import emoji
from nltk.corpus import stopwords
import os

os.environ["NLTK_DATA"] = "/usr/local/share/nltk_data"


def ensure_nltk_resources():
    """Ensure all required NLTK resources are available, including punkt_tab for newer NLTK versions"""
    required = {
        "punkt": "tokenizers/punkt",
        "punkt_tab": "tokenizers/punkt_tab", 
        "averaged_perceptron_tagger": "taggers/averaged_perceptron_tagger",
        "stopwords": "corpora/stopwords"
    }
    
    for name, path in required.items():
        try:
            nltk.data.find(path)
            print(f"✓ Found {name}")
        except LookupError:
            print(f"✗ Downloading {name}...")
            try:
                nltk.download(name, quiet=True)
                print(f"✓ Downloaded {name}")
            except Exception as e:
                print(f"✗ Failed to download {name}: {e}")
                # For punkt_tab, try punkt as fallback
                if name == "punkt_tab":
                    print("Trying punkt as fallback...")
                    try:
                        nltk.download("punkt", quiet=True)
                        print("✓ Downloaded punkt as fallback")
                    except Exception as fallback_e:
                        print(f"✗ Fallback also failed: {fallback_e}")


# Custom stopword set with negation sensitivity
try:
    STANDARD_STOPWORDS = set(stopwords.words("english"))
except LookupError:
    print("Stopwords not found, downloading...")
    nltk.download("stopwords", quiet=True)
    STANDARD_STOPWORDS = set(stopwords.words("english"))

NEGATION_WORDS = {
    "no", "nor", "not", "ain", "aren", "couldn", "didn", "doesn", "hadn",
    "hasn", "haven", "isn", "mightn", "mustn", "needn", "shan", "shouldn",
    "wasn", "weren", "won", "wouldn", "don", "don't", "aren't", "couldn't",
    "didn't", "doesn't", "hadn't", "hasn't", "haven't", "isn't", "mightn't",
    "mustn't", "needn't", "shan't", "shouldn't", "wasn't", "weren't", "won't", "wouldn't"
}
FILTERED_STOPWORDS = STANDARD_STOPWORDS - NEGATION_WORDS


class TextPreprocessor:
    """
    Cleans and standardizes informal text like Reddit comments.
    - Converts to lowercase
    - Handles contractions
    - Replaces emojis/emoticons with semantic meanings
    - Removes unwanted punctuation (preserves !, ?, /, #)
    - Filters custom stopwords
    """

    def __init__(self):
        pass

    def _remove_stopwords(self, text):
        return " ".join(word for word in text.split() if word not in FILTERED_STOPWORDS)

    def _expand_emojis_and_emoticons(self, text):
        # Demojize emojis into readable tokens
        demojized = emoji.demojize(text, delimiters=(" ", " "))
        # Optionally remove colons and convert underscores to spaces
        return re.sub(r":([a-z_]+):", lambda m: m.group(1).replace("_", " "), demojized)

    def clean(self, text):
        """
        Main text cleaning pipeline.
        """
        text = html.unescape(text)
        text = text.lower()
        text = contractions.fix(text)
        text = re.sub(r"http\S+|www\S+", "", text)  # remove URLs
        text = re.sub(r"<.*?>", "", text)            # remove HTML tags
        text = self._expand_emojis_and_emoticons(text)

        # Remove punctuation except symbols that might carry sentiment context
        safe_punct = set("!?/#")
        punct_to_remove = ''.join(ch for ch in string.punctuation if ch not in safe_punct)
        text = text.translate(str.maketrans(punct_to_remove, ' ' * len(punct_to_remove)))

        text = re.sub(r"\s+", " ", text).strip()
        text = self._remove_stopwords(text)

        return text


class SentimentScorer:
    """
    Assigns sentiment score (normalized between 0 and 1) to text using TextBlob.
    Intended for data labeling only.
    """

    def score(self, text):
        sentiment = TextBlob(text).sentiment.polarity
        return (sentiment + 1) / 2  # Normalize from [-1, 1] to [0, 1]


class NounExtractor:
    """
    Extracts noun words from input text using NLTK POS tagging.
    Useful for keyword-focused sentiment aggregation.
    """

    def extract(self, text):
        try:
            tokens = nltk.word_tokenize(text)
            tags = nltk.pos_tag(tokens)
            return [word for word, pos in tags if pos.startswith("NN")]
        except LookupError as e:
            print(f"NLTK tokenization error: {e}")
            # Fallback to simple whitespace tokenization
            words = text.split()
            return [word for word in words if len(word) > 2]  # Simple heuristic
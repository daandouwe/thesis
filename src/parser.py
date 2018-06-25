import torch

from data import EMPTY_INDEX, REDUCED_INDEX, EMPTY_TOKEN, REDUCED_TOKEN, PAD_TOKEN
from data import wrap

class Stack:
    """The stack"""
    def __init__(self, dictionary, word_embedding, nt_embedding):
        """Initialize the Stack.

        Args:
            dictionary: an instance of data.Dictionary
        """
        self._tokens = [] # list of strings
        self._indices = [] # list on indices
        self._embeddings = [] # list of embeddings (pytorch vectors)
        self._num_open_nonterminals = 0

        self.word_embedding = word_embedding
        self.nt_embedding = nt_embedding

    def __str__(self):
        return 'Stack ({} open NTs): {}'.format(self.num_open_nonterminals, self._tokens)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._tokens = []
        self._indices = []
        self._embeddings = []

    def initialize(self):
        self._reset()
        emb = self.word_embedding(wrap([EMPTY_INDEX]))
        self.push(EMPTY_TOKEN, EMPTY_INDEX, emb)

    def push(self, token, index, emb):
        self._tokens.append(token)
        self._indices.append(index)
        self._embeddings.append(emb)

    def open_nonterminal(self, token, index):
        self._num_open_nonterminals += 1
        self._tokens.append(token)
        self._indices.append(index)
        emb = self.nt_embedding(wrap([index]))
        self._embeddings.append(emb)

    def pop(self):
        """Pop tokens and vectors from the stack until first open nonterminal."""
        found_nonterminal = False
        tokens, indices, embeddings = [], [], []
        # We pop items from self._tokens till we find a nonterminal.
        while not found_nonterminal:
            token = self._tokens.pop()
            index = self._indices.pop()
            emb = self._embeddings.pop()
            tokens.append(token)
            indices.append(index)
            embeddings.append(emb)
            # Break from while if we found a nonterminal
            if token.startswith('NT'):
                found_nonterminal = True
        # reverse the lists (we appended)
        tokens = tokens[::-1]
        indices = indices[::-1]
        embeddings = embeddings[::-1]
        # add nonterminal also to the end of both lists
        tokens.append(tokens[0])
        indices.append(indices[0])
        embeddings.append(embeddings[0])
        # Package embeddings as pytorch tensor
        embs = [emb.unsqueeze(0) for emb in embeddings]
        embeddings = torch.cat(embs, 1) # [batch, seq_len, emb_dim]
        # Update the number of open nonterminals
        self._num_open_nonterminals -= 1
        return tokens, indices, embeddings

    @property
    def top_embedded(self):
        """Returns the embedding of the symbol on the top of the stack."""
        return self._embeddings[-1]

    @property
    def empty(self):
        return self._tokens == [EMPTY_TOKEN, REDUCED_TOKEN]

    @property
    def num_open_nonterminals(self):
        return self._num_open_nonterminals

class Buffer:
    """The buffer."""
    def __init__(self, dictionary, embedding):
        self._tokens = []
        self._indices = []
        self._embeddings = []
        self._hiddens = []

        self.dict = dictionary
        self.embedding = embedding

    def __str__(self):
        return 'Buffer : {}'.format(self._tokens)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._tokens = []
        self._indices = []
        self._embeddings = []
        self._hiddens = []

    def initialize(self, sentence, indices):
        """Initialize buffer by loading in the sentence in reverse order."""
        self._reset()
        for token, index in zip(sentence[::-1], indices[::-1]):
            self.push(token, index)

    def push(self, token, index):
        """Push action index and vector embedding onto buffer."""
        self._tokens.append(token)
        self._indices.append(index)
        vec = self.embedding(wrap([index]))
        self._embeddings.append(vec)
        self._hiddens.append(vec)

    def pop(self):
        if self.empty:
            raise ValueError('trying to pop from an empty buffer')
        else:
            token = self._tokens.pop()
            index = self._indices.pop()
            vec = self._embeddings.pop()
            _ = self._hiddens.pop() # We do not need this one
            # If this pop makes the buffer empty, push
            # the empty token to signal that it is empty.
            if not self._tokens:
                self.push(EMPTY_TOKEN, EMPTY_INDEX)
            return token, index, vec

    def encode(self, lstm_encoder):
        """Use the encoder to make a list of encodings for the """
        x = self.embedded # [batch, seq, hidden_size]
        h, _ = lstm_encoder(x) # [batch, seq, hidden_size]
        self._hiddens = [h[:, i ,:] for i in range(h.size(1))]

    @property
    def embedded(self):
        """Concatenate all the embeddings and return as pytorch tensor"""
        embs = [emb.unsqueeze(0) for emb in self._embeddings]
        return torch.cat(embs, 1) # [batch, seq_len, emb_dim]

    @property
    def top_embedded(self):
        """Returns the embedding of the symbol on the top of the buffer."""
        return self._embeddings[-1]

    @property
    def empty(self):
        return self._tokens == [EMPTY_TOKEN]

class History:
    def __init__(self, dictionary, embedding):
        self._actions = []
        self._embeddings = []

        self.dict = dictionary
        self.embedding = embedding

    def __str__(self):
        history = self.actions
        return 'History : {}'.format(history)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._actions = []
        self._embeddings = []

    def initialize(self):
        self._reset()
        self.push(EMPTY_INDEX)

    def push(self, action):
        """Push action index and vector embedding onto history."""
        self._actions.append(action)
        self._embeddings.append(self.embedding(wrap([action])))

    @property
    def embedded(self):
        """Concatenate all the embeddings and return as pytorch tensor"""
        embs = [emb.unsqueeze(0) for emb in self._embeddings]
        return torch.cat(embs, 1) # [batch, seq_len, emb_dim]

    @property
    def top_embedded(self):
        """Returns the embedding of the symbol on the top of the stack."""
        return self._embeddings[-1]

    @property
    def last_action(self):
        i = self._actions[-1]
        return self.dict.i2a[i]

    @property
    def actions(self):
        return [self.dict.i2a[i] for i in self._actions]

class Parser:
    """The parse configuration."""
    def __init__(self, dictionary, word_embedding, nt_embedding, action_embedding):
        self.stack = Stack(dictionary, word_embedding, nt_embedding)
        self.buffer = Buffer(dictionary, word_embedding)
        self.history = History(dictionary, action_embedding)
        self.dict = dictionary

    def __str__(self):
        return 'PARSER STATE\n{}\n{}\n{}'.format(self.stack, self.buffer, self.history)

    def initialize(self, sentence, indices):
        """Initialize all the components of the parser."""
        self.stack.initialize()
        self.buffer.initialize(sentence, indices)
        self.history.initialize()

    def shift(self):
        token, index, emb = self.buffer.pop()
        self.stack.push(token, index, emb)

    def get_embedded_input(self):
        stack = self.stack.top_embedded # input on top [batch, emb_size]
        buffer = self.buffer.top_embedded # input on top [batch, emb_size]
        history = self.history.top_embedded # input on top [batch, emb_size]
        return stack, buffer, history

    def is_valid_action(self, action):
        """Check whether the action is valid under the parser's configuration."""
        if action == 'SHIFT':
            cond1 = not self.buffer.empty
            cond2 = self.stack.num_open_nonterminals > 0
            return cond1 and cond2
        elif action =='REDUCE':
            cond1 = not self.history.last_action.startswith('NT')
            cond2 = self.stack.num_open_nonterminals > 1
            cond3 = self.buffer.empty
            return cond1 and (cond2 or cond3)
        elif action.startswith('NT'):
            cond1 = not self.buffer.empty
            cond2 = self.stack.num_open_nonterminals < 100
            return cond1 and cond2
        # TODO: Fix this in the Dictionary class in data.py
        # elif action in [PAD_TOKEN, EMPTY_TOKEN, REDUCED_TOKEN]:
            # return False
        else:
            raise ValueError('got illegal action: {}'.format(action))

    @property
    def actions(self):
        return self.history.actions

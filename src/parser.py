import torch

from data import (EMPTY_INDEX, REDUCED_INDEX, EMPTY_TOKEN, REDUCED_TOKEN, PAD_TOKEN,
                    wrap, pad)

class Stack:
    """The stack."""
    def __init__(self, dictionary, word_embedding, nonterminal_embedding, device):
        """Initialize the Stack.

        Arguments:
            dictionary (data.Dictionary): wraps dictionaries.
            word_embedding (nn.Embedding): embedding function for words.
            nonterminal_embedding (nn.Embedding): embedding function for nonterminals.
            device: device on which computation is done (gpu or cpu).
        """
        self._tokens = [] # list of str
        self._indices = [] # list of int
        self._embeddings = [] # list of embeddings (pytorch Variables)
        self._num_open_nonterminals = 0
        self.word_embedding = word_embedding
        self.nonterminal_embedding = nonterminal_embedding
        self.device = device

    def __str__(self):
        return 'Stack ({self.num_open_nonterminals} open NTs): {self._tokens}'.format(self=self)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._tokens = []
        self._indices = []
        self._embeddings = []

    def initialize(self):
        """Initialize by pushing the `empty` item onto the stack."""
        self._reset()
        empty_embedding = self.word_embedding(wrap([EMPTY_INDEX], self.device))
        self.push(EMPTY_TOKEN, EMPTY_INDEX, empty_embedding)

    def push(self, token, index, emb):
        """Push a new item on the stack."""
        self._tokens.append(token)
        self._indices.append(index)
        self._embeddings.append(emb)

    def open_nonterminal(self, token, index):
        """Open a new nonterminal in the tree."""
        self._num_open_nonterminals += 1
        emb = self.nonterminal_embedding(wrap([index], self.device))
        self.push(token, index, emb)

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
        """Returns True if the stack is empty"""
        return self._tokens == [EMPTY_TOKEN, REDUCED_TOKEN]

    @property
    def num_open_nonterminals(self):
        """Return the number of nonterminal nodes in the tree that are not yet closed."""
        return self._num_open_nonterminals

class Buffer:
    """The buffer."""
    def __init__(self, dictionary, embedding, encoder, device):
        """Initialize the Buffer.

        Arguments:
            dictionary (data.Dictionary): wraps dictionaries.
            embedding (nn.Embedding): embedding function for words on the buffer.
            encoder (nn.Module): encoder function to encode buffer contents.
            device: device on which computation is done (gpu or cpu).
        """
        self._tokens = [] # list of str
        self._indices = [] # list of int
        self._embeddings = [] # list of embeddings (pytorch Variables)
        self._hiddens = [] # list of embeddings (pytorch Variables)
        self.dict = dictionary
        self.embedding = embedding
        self.encoder = encoder
        self.device = device

    def __str__(self):
        return 'Buffer : {self._tokens}'.format(self=self)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._tokens = []
        self._indices = []
        self._embeddings = []
        self._hiddens = []

    def initialize(self, sentence, indices):
        """Initialize buffer with te sentence."""
        self._reset()
        self._tokens = sentence[::-1]
        self._indices = indices[::-1]
        embeddings = self.embedding(wrap(self._indices, self.device))
        self._embedded = embeddings.unsqueeze(0)
        self._embeddings = [embeddings[i, :].unsqueeze(0)
                                for i in range(embeddings.size(0))]

    def push(self, token, index):
        """Push action index and vector embedding onto buffer."""
        self._tokens.append(token)
        self._indices.append(index)
        emb = self.embedding(wrap([index], self.device))
        self._embeddings.append(emb)

    def pop(self):
        if self.empty:
            raise ValueError('trying to pop from an empty buffer')
        else:
            token = self._tokens.pop()
            index = self._indices.pop()
            emb = self._embeddings.pop()
            _ = self._hiddens.pop() # We do not need this one
            # If pop makes the buffer empty, push
            # the empty token to signal that it is empty.
            if not self._tokens:
                self.push(EMPTY_TOKEN, EMPTY_INDEX)
            return token, index, emb

    def encode(self):
        """Encode the buffer with the provided encoder and store these internally."""
        x = self._embedded # [batch, seq, hidden_size]
        h = self.encoder(x) # [batch, seq, hidden_size]
        self._hiddens = [h[:, i ,:] for i in range(h.size(1))]
        del self._embedded

    @property
    def embedded(self):
        """Concatenate all the embeddings and return as pytorch tensor."""
        embs = [emb.unsqueeze(0) for emb in self._embeddings]
        return torch.cat(embs, 1) # [batch, seq_len, emb_dim]

    @property
    def top_embedded(self):
        """Returns the embedding of the symbol on the top of the buffer."""
        return self._embeddings[-1]

    @property
    def empty(self):
        """Returns True if the buffer is empty."""
        return self._tokens == [EMPTY_TOKEN]

class History:
    """The history."""
    def __init__(self, dictionary, embedding, device):
        """Initialize the Buffer.

        Arguments:
            dictionary (data.Dictionary): wraps dictionaries.
            embedding (nn.Embedding): embedding function for actions.
            device: device on which computation is done (gpu or cpu).
        """
        self._actions = [] # list of int
        self._embeddings = []# list of embeddings (pytorch Variables)
        self.dict = dictionary
        self.embedding = embedding
        self.device = device

    def __str__(self):
        return 'History : {self.actions}'.format(self=self)

    def _reset(self):
        """Resets the buffer to empty state."""
        self._actions = []
        self._embeddings = []

    def initialize(self):
        """Initialize the history by push the `empty` item."""
        self._reset()
        self.push(EMPTY_INDEX)

    def push(self, action):
        """Push action index and vector embedding onto history."""
        self._actions.append(action)
        self._embeddings.append(self.embedding(wrap([action], self.device)))

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
        """Return the last action taken."""
        i = self._actions[-1]
        return self.dict.i2a[i]

    @property
    def actions(self):
        """Return the current history of actions."""
        return [self.dict.i2a[i] for i in self._actions]

class Parser:
    """The parse configuration."""
    def __init__(self, dictionary, word_embedding, nonterminal_embedding, action_embedding,
                 buffer_encoder, device):
        """Initialize the parser.

        Arguments:
            dictionary (data.Dictionary): wraps dictionaries.
            word_embedding (nn.Embedding): embedding function for words.
            nonterminal_embedding (nn.Embedding): embedding function for nonterminals.
            actions_embedding (nn.Embedding): embedding function for actions.
            buffer_encoder (nn.Module): encoder function to encode buffer contents.
            device: device on which computation is done (gpu or cpu).
        """
        self.stack = Stack(dictionary, word_embedding, nonterminal_embedding, device)
        self.buffer = Buffer(dictionary, word_embedding, buffer_encoder, device)
        self.history = History(dictionary, action_embedding, device)
        self.dict = dictionary

    def __str__(self):
        return '\n'.join(('PARSER STATE', str(self.stack), str(self.buffer), str(self.history)))

    def initialize(self, sentence, indices):
        """Initialize all the components of the parser."""
        self.buffer.initialize(sentence, indices)
        self.stack.initialize()
        self.history.initialize()

    def shift(self):
        """Shift an item from the buffer to the stack."""
        token, index, emb = self.buffer.pop()
        self.stack.push(token, index, emb)

    def get_embedded_input(self):
        """Return the representations of the stack buffer and history."""
        stack = self.stack.top_embedded     # [batch, emb_size]
        buffer = self.buffer.top_embedded   # [batch, emb_size]
        history = self.history.top_embedded # [batch, emb_size]
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
        else:
            raise ValueError('got illegal action: {action}'.format(action=action))

    @property
    def actions(self):
        """Return the current history of actions."""
        return self.history.actions


if __name__ == '__main__':
    stack = Stack(None, None, None, device=None)
    parser = Parser(None, None, None, None, None, device=None)
    print(stack)
    print(parser)

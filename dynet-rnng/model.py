import dynet as dy
import numpy as np

from parser import DiscParser, GenParser, Stack, Buffer, History, Terminal
from embedding import Embedding, FineTuneEmbedding, PretrainedEmbedding
from encoder import StackLSTM
from composition import BiRecurrentComposition, AttentionComposition
from feedforward import Feedforward


class DiscRNNG(DiscParser):
    """Discriminative Recurrent Neural Network Grammar."""
    def __init__(
            self,
            model,
            dictionary,
            num_words,
            num_nt,
            word_emb_dim,
            nt_emb_dim,
            action_emb_dim,
            stack_lstm_dim,
            buffer_lstm_dim,
            history_lstm_dim,
            lstm_layers,
            composition,
            f_hidden_dim,
            dropout,
            use_glove=False,
            glove_dir=None,
            fine_tune_embeddings=False,
            freeze_embeddings=False
    ):
        self.spec = locals()
        self.spec.pop("self")
        self.spec.pop("model")

        self.model = model.add_subcollection('DiscRNNG')
        self.num_words = num_words
        self.num_nt = num_nt
        self.num_actions = 2 + num_nt

        self.word_emb_dim = word_emb_dim
        self.nt_emb_dim = nt_emb_dim
        self.action_emb_dim = action_emb_dim

        self.dictionary = dictionary

        # Embeddings
        self.nt_embedding = Embedding(self.model, num_nt, nt_emb_dim)
        self.action_embedding = Embedding(self.model, self.num_actions, action_emb_dim)
        if use_glove:
            if fine_tune_embeddings:
                self.word_embedding = FineTuneEmbedding(
                    self.model, num_words, word_emb_dim, glove_dir, dictionary.i2w)
            else:
                self.word_embedding = PretrainedEmbedding(
                    self.model, num_words, word_emb_dim, glove_dir, dictionary.i2w, freeze=freeze_embeddings)
        else:
            self.word_embedding = Embedding(self.model, num_words, word_emb_dim)

        # Encoders
        self.stack_encoder = StackLSTM(
            self.model, word_emb_dim, stack_lstm_dim, lstm_layers, dropout)
        self.buffer_encoder = StackLSTM(
            self.model, word_emb_dim, buffer_lstm_dim, lstm_layers, dropout)
        self.history_encoder = StackLSTM(
            self.model, action_emb_dim, history_lstm_dim, lstm_layers, dropout)

        # Composition function
        if composition == 'basic':
            self.composer = BiRecurrentComposition(
                self.model, word_emb_dim, lstm_layers, dropout)
        elif composition == 'attention':
            self.composer = AttentionComposition(
                self.model, word_emb_dim, lstm_layers, dropout)

        # Embeddings for empty transition system
        stack_empty_emb = self.model.add_parameters(word_emb_dim, init='glorot')
        buffer_empty_emb = self.model.add_parameters(word_emb_dim, init='glorot')
        history_empty_emb = self.model.add_parameters(action_emb_dim, init='glorot')

        # Transition system
        self.stack = Stack(
            dictionary, self.word_embedding, self.nt_embedding, self.stack_encoder, self.composer, stack_empty_emb)
        self.buffer = Buffer(
            dictionary, self.word_embedding, self.buffer_encoder, buffer_empty_emb)
        self.history = History(
            dictionary, self.action_embedding, self.history_encoder, history_empty_emb)

        # Scorers
        parser_dim = stack_lstm_dim + buffer_lstm_dim + history_lstm_dim
        self.f_action = Feedforward(self.model, parser_dim, [f_hidden_dim], self.num_actions)

    def param_collection(self):
        return self.model

    @classmethod
    def from_spec(cls, spec, model):
        return cls(model, **spec)

    @property
    def components(self):
        return (
            self.stack_encoder,
            self.buffer_encoder,
            self.history_encoder,
            self.composer,
            self.f_action
        )

    def train(self):
        """Enable dropout."""
        for component in self.components:
            component.train()

    def eval(self):
        """Disable dropout."""
        for component in self.components:
            component.eval()

    def __call__(self, words, actions):
        """Forward pass for training."""
        self.initialize(words)
        nll = 0.
        for action_id in actions:
            # Compute action loss
            u = self.parser_representation()
            action_logits = self.f_action(u)
            nll += dy.pickneglogsoftmax(action_logits, action_id)
            # Move the parser ahead.
            self.parse_step(self.dictionary.i2a[action_id], action_id)
        return nll

    def parse(self, words):
        """Greedy decoding for prediction."""
        self.eval()
        nll = 0.
        self.initialize(words)
        while not self.stack.is_finished():
            u = self.parser_representation()
            action_logits = self.f_action(u)
            action_id = np.argmax(action_logits.value() + self._add_actions_mask())
            nll += dy.pickneglogsoftmax(action_logits, action_id)
            self.parse_step(self.dictionary.i2a[action_id], action_id)
        return self.get_tree(), nll

    def sample(self, words, alpha=1.):
        """Ancestral sampling."""
        def compute_probs(logits):
            probs = np.array(dy.softmax(logits).value()) * self._mult_actions_mask()
            if alpha != 1.:
                probs = probs**alpha
            probs /= probs.sum()
            return probs

        self.eval()
        nll = 0.
        self.initialize(words)
        while not self.stack.is_finished():
            u = self.parser_representation()
            action_logits = self.f_action(u)
            action_id = np.random.choice(
                np.arange(self.num_actions), p=compute_probs(action_logits))
            nll += dy.pickneglogsoftmax(action_logits, action_id)
            self.parse_step(self.dictionary.i2a[action_id], action_id)
        return self.get_tree(), nll


class GenRNNG(GenParser):
    """Discriminative Recurrent Neural Network Grammar."""
    def __init__(
            self,
            model,
            dictionary,
            num_words,
            num_nt,
            word_emb_dim,
            nt_emb_dim,
            action_emb_dim,
            stack_lstm_dim,
            terminal_lstm_dim,
            history_lstm_dim,
            lstm_layers,
            composition,
            f_hidden_dim,
            dropout,
            use_glove=False,
            glove_dir=None,
            fine_tune_embeddings=False,
            freeze_embeddings=False
    ):
        self.spec = locals()
        self.spec.pop("self")
        self.spec.pop("model")

        self.model = model.add_subcollection('GenRNNG')
        self.num_words = num_words
        self.num_nt = num_nt
        self.num_actions = 1 + num_nt + num_words

        self.word_emb_dim = word_emb_dim
        self.nt_emb_dim = nt_emb_dim
        self.action_emb_dim = action_emb_dim

        self.dictionary = dictionary

        # Embeddings
        self.nt_embedding = Embedding(self.model, num_nt, nt_emb_dim)
        self.action_embedding = Embedding(self.model, self.num_actions, action_emb_dim)
        if use_glove:
            if fine_tune_embeddings:
                self.word_embedding = FineTuneEmbedding(
                    self.model, num_words, word_emb_dim, glove_dir, dictionary.i2w)
            else:
                self.word_embedding = PretrainedEmbedding(
                    self.model, num_words, word_emb_dim, glove_dir, dictionary.i2w, freeze=freeze_embeddings)
        else:
            self.word_embedding = Embedding(self.model, num_words, word_emb_dim)

        # Encoders
        self.stack_encoder = StackLSTM(
            self.model, word_emb_dim, stack_lstm_dim, lstm_layers, dropout)
        self.terminal_encoder = StackLSTM(
            self.model, word_emb_dim, terminal_lstm_dim, lstm_layers, dropout)
        self.history_encoder = StackLSTM(
            self.model, action_emb_dim, history_lstm_dim, lstm_layers, dropout)

        # Composition function
        if composition == 'basic':
            self.composer = BiRecurrentComposition(
                self.model, word_emb_dim, lstm_layers, dropout)
        elif composition == 'attention':
            self.composer = AttentionComposition(
                self.model, word_emb_dim, lstm_layers, dropout)

        # Embeddings for empty transition system
        stack_empty_emb = model.add_parameters(word_emb_dim, init='glorot')
        terminal_empty_emb = model.add_parameters(word_emb_dim, init='glorot')
        history_empty_emb = model.add_parameters(action_emb_dim, init='glorot')

        # Transition system
        self.stack = Stack(
            dictionary, self.word_embedding, self.nt_embedding, self.stack_encoder, self.composer, stack_empty_emb)
        self.terminal = Terminal(
            dictionary, self.word_embedding, self.terminal_encoder, terminal_empty_emb)
        self.history = History(
            dictionary, self.action_embedding, self.history_encoder, history_empty_emb)

        # Scorers
        parser_dim = stack_lstm_dim + terminal_lstm_dim + history_lstm_dim
        self.f_action = Feedforward(self.model, parser_dim, [f_hidden_dim], 3)  # REDUCE, NT, GEN
        self.f_nt = Feedforward(self.model, parser_dim, [f_hidden_dim], self.num_nt)  # S, NP, ...
        self.f_word = Feedforward(self.model, parser_dim, [f_hidden_dim], self.num_words)  # the, cat, ...

    def param_collection(self):
        return self.model

    @classmethod
    def from_spec(cls, spec, model):
        return cls(model, **spec)

    @property
    def components(self):
        return (
            self.stack_encoder,
            self.terminal_encoder,
            self.history_encoder,
            self.composer,
            self.f_action,
            self.f_nt,
            self.f_word
        )

    def train(self):
        """Enable dropout."""
        for component in self.components:
            component.train()

    def eval(self):
        """Disable dropout."""
        for component in self.components:
            component.eval()

    def __call__(self, words, actions):
        """Forward pass for training."""
        self.initialize()
        nll = 0.
        for action_id in actions:
            # Compute action loss
            u = self.parser_representation()
            action_logits = self.f_action(u)
            nll += dy.pickneglogsoftmax(action_logits, self._get_action_id(action_id))
            if self._is_nt_id(action_id):
                nt_logits = self.f_nt(u)
                nll += dy.pickneglogsoftmax(nt_logits, self._get_nt_id(action_id))
            elif self._is_gen_id(action_id):
                word_logits = self.f_word(u)
                nll += dy.pickneglogsoftmax(word_logits, self._get_word_id(action_id))
            # Move the parser ahead.
            self.parse_step(self.dictionary.i2a[action_id], action_id)
        return nll

    def sample(self, alpha=1.):
        """Ancestral sampling."""
        def compute_probs(logits, mult_mask=None):
            probs = np.array(dy.softmax(logits).value())
            if mult_mask is not None:
                probs = probs * mult_mask
            if alpha != 1.:
                probs = probs**alpha
            probs /= probs.sum()
            return probs

        self.eval()
        self.initialize()
        nll = 0.
        while not self.stack.is_finished():
            # Compute action loss
            u = self.parser_representation()
            action_logits = self.f_action(u)
            action_id = np.random.choice(
                np.arange(3), p=compute_probs(action_logits, mult_mask=self._mult_actions_mask()))
            nll += dy.pickneglogsoftmax(action_logits, action_id)
            if action_id == self.NT_ID:
                nt_logits = self.f_nt(u)
                nt_id = np.random.choice(
                    np.arange(self.num_nt), p=compute_probs(nt_logits))
                nll += dy.pickneglogsoftmax(nt_logits, nt_id)
                action_id = self._make_action_id_from_nt_id(nt_id)
            elif action_id == self.GEN_ID:
                word_logits = self.f_word(u)
                word_id = np.random.choice(
                    np.arange(self.num_words), p=compute_probs(word_logits))
                nll += dy.pickneglogsoftmax(word_logits, word_id)
                action_id = self._make_action_id_from_word_id(word_id)
            # Move the parser ahead.
            self.parse_step(self.dictionary.i2a[action_id], action_id)
        return self.get_tree(), nll

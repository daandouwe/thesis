from typing import NamedTuple

import torch
import torch.nn as nn
from torch import Tensor

import matchbox
import matchbox.functional as F

from tree import Node, InternalNode, LeafNode


class StackElement(NamedTuple):
    id: Tensor  # [1]
    emb: Tensor  # [1, emb_dim]
    subtree: Node
    is_open_nt: bool


class Stack(nn.Module):
    def __init__(self, dictionary, word_embedding, nt_embedding, encoder, composer, device):
        super(Stack, self).__init__()
        assert (word_embedding.embedding_dim == nt_embedding.embedding_dim)

        self.dictionary = dictionary
        self.device = device
        self.embedding_dim = word_embedding.embedding_dim
        self.word_embedding = word_embedding
        self.nt_embedding = nt_embedding
        self.encoder = encoder
        self.composer = composer
        self.empty_emb = nn.Parameter(torch.zeros(1, self.embedding_dim, device=device))
        self._stack = []
        self._num_open_nts = 0

    def state(self):
        return f'Stack: {self.get_tree()}'

    def initialize(self):
        self._stack = []
        self._num_open_nts = 0
        self.encoder.initialize()
        self.encoder.push(self.empty_emb)

    def open(self, nt_id: Tensor):  # [1]
        emb = self.nt_embedding(nt_id)  # [1, emb_dim]
        self.encoder.push(emb)
        subtree = InternalNode(self.dictionary.i2n[nt_id.item()])
        self.attach_subtree(subtree)
        self._stack.append(StackElement(nt_id, emb, subtree, True))
        self._num_open_nts += 1

    def push(self, word_id: Tensor):  # [1]
        emb = self.word_embedding(word_id)  # [1, emb_dim]
        self.encoder.push(emb)
        subtree = LeafNode(self.dictionary.i2w[word_id.item()])
        self.attach_subtree(subtree)
        self._stack.append(StackElement(word_id, emb, subtree, False))

    def pop(self):
        return self._stack.pop()

    def attach_subtree(self, subtree):
        """Add subtree as rightmost child to rightmost open nonterminal."""
        for node in self._stack[::-1]:
            if node.is_open_nt:
                node.subtree.add_child(subtree)
                break

    def reduce(self):
        # Gather children.
        children = []
        while not self._stack[-1].is_open_nt:
            children.append(self.pop())
        children.reverse()
        # Get head.
        head = self.pop()
        # Gather child embeddings.
        sequence_len = len(children) + 1
        child_embeddings = [child.emb.unsqueeze(0) for child in children]
        child_embeddings = torch.cat(child_embeddings, dim=1)  # tensor (1, seq_len, emb_dim)
        head_embedding = head.emb  # tensor (1, emb_dim)
        # Compute new representation.
        reduced_emb = self.composer(head_embedding, child_embeddings)
        # Pop hidden states from StackLSTM.
        for _ in range(sequence_len):
            self.encoder.pop()
        # Reencode with reduce embedding.
        self.encoder.push(reduced_emb)
        self._stack.append(StackElement(head.id, reduced_emb, head.subtree, False))
        self._num_open_nts -= 1

    def get_tree(self):
        if self.is_empty():
            return '()'
        else:
            return self._stack[0].subtree

    def is_empty(self):
        return len(self._stack) == 0

    def is_finished(self):
        return self._stack[0].is_open_nt # (S needs to be closed)

    @property
    def num_open_nts(self):
        return self._num_open_nts


class Buffer(nn.Module):
    def __init__(self, dictionary, embedding, encoder, device):
        super(Buffer, self).__init__()
        self.dictionary = dictionary
        self.embedding_dim = embedding.embedding_dim
        self.embedding = embedding
        self.encoder = encoder
        self.device = device
        self.empty_emb = nn.Parameter(torch.zeros(1, self.embedding_dim, device=self.device))
        self._buffer = []

    def state(self):
        words = [self.dictionary.i2w[word_id.item()] for word_id in self._buffer]
        return f'Buffer: {words}'

    def initialize(self, sentence: Tensor):
        """Embed and encode the sentence."""
        self._buffer = []
        self.encoder.initialize()
        self.encoder.push(self.empty_emb)
        for word_id in reversed(sentence.unbind(1)):  # [1]
            self._buffer.append(word_id)
            self.encoder.push(self.embedding(word_id))

    def pop(self):
        self.encoder.pop()
        return self._buffer.pop()

    def is_empty(self):
        return len(self._buffer) == 0


class History(nn.Module):

    def __init__(self, dictionary, embedding, encoder, device):
        super(History, self).__init__()
        self.dictionary = dictionary
        self.embedding_dim = embedding.embedding_dim
        self.embedding = embedding
        self.encoder = encoder
        self.device = device
        self.empty_emb = nn.Parameter(torch.zeros(1, self.embedding_dim, device=device))
        self._history = []

    def state(self):
        actions = [self.dictionary.i2a[action_id.item()] for action_id in self._history]
        return f'History: {actions}'

    def initialize(self):
        self._history = []
        self.encoder.initialize()
        self.encoder.push(self.empty_emb)

    def push(self, action_id: Tensor):  # [1]
        self._history.append(action_id)
        self.encoder.push(self.embedding(action_id))

    @property
    def actions(self):
        return self._history

    @property
    def is_empty(self):
        return len(self._history) == 0


class DiscParser(nn.Module):

    def __init__(self):
        super(DiscParser, self).__init__()

    def state(self):
        return '\n'.join(
            (f'Parser', self.stack.state(), self.buffer.state(), self.history.state(), ''))

    def initialize(self, sentence):
        """Initialize all the components of the parser."""
        self.buffer.initialize(sentence)
        self.stack.initialize()
        self.history.initialize()

    def _can_shift(self):
        cond1 = not self.buffer.is_empty()
        cond2 = self.stack.num_open_nts >= 1
        return cond1 and cond2

    def _can_open(self):
        cond1 = not self.buffer.is_empty()
        cond2 = self.stack.num_open_nts < 100
        return cond1 and cond2

    def _can_reduce(self):
        cond1 = not self._is_nt(self.last_action)  # TODO
        cond2 = self.stack.num_open_nts >= 2
        cond3 = self.buffer.is_empty()
        return cond1 and (cond2 or cond3)

    def _shift(self):
        assert self._can_shift(), f'cannot shift: {self}'
        self.stack.push(self.buffer.pop())

    def _open(self, nt_index: Tensor):
        assert self._can_open(), f'cannot open: {self}'
        self.stack.open(nt_index)

    def _reduce(self):
        assert self._can_reduce(), f'cannot reduce: {self}'
        self.stack.reduce()

    def parser_representation(self):
        """Return the representations of the stack, buffer and history."""
        s = self.stack.encoder.top    # (1, stack_lstm_hidden)
        b = self.buffer.encoder.top   # (1, buffer_lstm_hidden)
        h = self.history.encoder.top  # (1, action_lstm_hidden)
        return torch.cat((s, b, h), dim=-1)

    def parse_step(self, action: str, action_id: Tensor):
        """Updates parser one step give the action."""
        if action == 'SHIFT':
            self._shift()
        elif action == 'REDUCE':
            self._reduce()
        else:
            self._open(self._get_nt(action_id))
        self.history.push(action_id)

    def is_valid_action(self, action: str):
        """Check whether the action is valid under the parser's configuration."""
        if action == 'SHIFT':
            return self._can_shift()
        elif action == 'REDUCE':
            return self._can_reduce()
        else:
            return self._can_open()

    def _is_nt(self, action_id):
        return action_id >= 2

    def _get_nt(self, action_id):
        assert self._is_nt(action_id)
        return action_id - 2

    @property
    def actions(self):
        """Return the current history of actions."""
        return self.history.actions

    @property
    def last_action(self):
        """Return the last action taken."""
        return self.history.actions[-1]
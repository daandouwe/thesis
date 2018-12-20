#!/usr/bin/env python
import os
import argparse
from math import inf

import train
import predict
import inspect_model
import latent_model

def main():

    parser = argparse.ArgumentParser(description='RNNG parser',
                                     fromfile_prefix_chars='@') # Enable loading args from textfile
    # Choose mode
    parser.add_argument('mode', choices=['train', 'predict', 'inspect', 'latent'],
                        help='what would you like to do?')
    parser.add_argument('model', choices=['disc', 'gen'],
                        help='use discriminative or generative model')

    # Debugging
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-m', '--memory-debug', action='store_true')

    # Data arguments
    parser.add_argument('--data', type=str, default='../data',
                        help='location of the data corpus')
    parser.add_argument('--textline', type=str, choices=['unked', 'lower', 'original'], default='unked',
                        help='textline to use from the oracle file')
    parser.add_argument('--name', type=str, default='ptb',
                        help='name of dataset for ptb.train.oracle, ptb.test.trees, etc.')
    parser.add_argument('--root', type=str, default='.',
                        help='root dir to make output log and checkpoint folders')
    parser.add_argument('--disable-subdir', action='store_true',
                        help='do not make subdirectory inside `logdir`, `checkdir` and `outdir`')
    parser.add_argument('--logdir', default='log',
                        help='directory for logs')
    parser.add_argument('--outdir', default='out',
                        help='directory for predictions')
    parser.add_argument('--checkdir', default='checkpoints',
                        help='directory to save models')
    parser.add_argument('--disable-folders', action='store_true',
                        help='do not make output folders (debug)')
    parser.add_argument('--max-lines', default=-1, type=int,
                        help='max number of training lines')

    # Model arguments
    parser.add_argument('--use-chars', action='store_true',
                        help='use character-level word embeddings')
    parser.add_argument('--emb-dim', type=int, default=100,
                        help='dim of all embeddings (words, actions, nonterminals)')
    parser.add_argument('--word-lstm-hidden', type=int, default=128,
                        help='size of lstm hidden states for StackLSTM and BufferLSTM')
    parser.add_argument('--action-lstm-hidden', type=int, default=128,
                        help='size of lstm hidden states for history encoder')
    parser.add_argument('--lstm-num-layers', type=int, default=2,
                        help='number of layers in lstm')
    parser.add_argument('--composition', default='attention', choices=['basic', 'attention', 'latent-factors'],
                        help='composition function used by StackLSTM')
    parser.add_argument('--mlp-dim', type=int, default=128,
                        help='size of mlp hidden state')
    parser.add_argument('--mlp_nonlinearity', default='Tanh', choices=['Tanh', 'ReLU'],
                        help='nonlinear function inside mlp')
    parser.add_argument('--use-glove', action='store_true',
                        help='using pretrained glove embeddings')
    parser.add_argument('--use-fasttext', action='store_true',
                        help='using pretrained fasttext embeddings')
    parser.add_argument('--glove-dir', type=str, default='~/embeddings/glove',
                        help='to be constructed in main')
    parser.add_argument('--glove-torchtext', action='store_true',
                        help='loading glove with torchtext instead of custom loader')
    parser.add_argument('--fine-tune-embeddings', action='store_true',
                        help='train minimal additive refinement of pretrained embeddings')
    parser.add_argument('--freeze-embeddings', action='store_true',
                        help='do not train or fine-tune embeddings')

    # Training arguments
    parser.add_argument('--seed', type=int, default=42,
                        help='random seed to use')
    parser.add_argument('--max-epochs', type=int, default=inf,
                        help='max number of epochs')
    parser.add_argument('--max-time', type=int, default=inf,
                        help='max time in seconds')
    parser.add_argument('--batch-size', type=int, default=1,
                        help='size of mini batch')
    parser.add_argument('--dropout', type=float, default=0.2,
                        help='dropout rate for embeddings, lstm, and mlp')
    parser.add_argument('--weight-decay', type=float, default=1e-3,
                        help='weight decay (also when using dropout!)')
    parser.add_argument('--optimizer', choices=['adam', 'sgd', 'rmsprop'], default='adam',
                        help='optimizer used')
    parser.add_argument('--lr', type=float, default=0.001,
                        help='initial learning rate')
    parser.add_argument('--momentum', type=float, default=0,
                        help='momentum for sgd')
    parser.add_argument('--disable-kl-anneal', action='store_false',
                        help='do not anneal the kl in the elbo objective')
    parser.add_argument('--disable-glorot', action='store_true',
                        help='do not override custom lstm initialization with glorot')
    parser.add_argument('--clip', type=float, default=5.,
                        help='clipping gradient norm at this value')
    parser.add_argument('--print-every', type=int, default=10,
                        help='when to print training progress')
    parser.add_argument('--eval-every', type=int, default=-1,
                        help='evaluate model on development (default: every epoch (-1))')
    parser.add_argument('--disable-cuda', action='store_true',
                        help='disable cuda')
    parser.add_argument('--num-procs', type=int, default=1,
                        help='number of processes to spawn for parallel training')
    parser.add_argument('--dev-proposal-samples', type=str, default='../data/proposal-samples/dev.props',
                        help='proposal samples for development set')
    parser.add_argument('--test-proposal-samples', type=str, default='../data/proposal-samples/test.props',
                        help='proposal samples for test set')


    # Predict arguments
    parser.add_argument('--checkpoint', type=str, default='',
                        help='load model from this checkpoint')
    parser.add_argument('--proposal-model', type=str, default='',
                        help='load discriminative model (proposal for generative model) from this checkpoint')
    parser.add_argument('--proposal-samples', type=str, default='../data/proposal-samples',
                        help='load proposal samples')
    parser.add_argument('--from-input', action='store_true',
                        help='predict for user input')
    parser.add_argument('--from-file', action='store_true',
                        help='predict for user input')
    parser.add_argument('--sample-gen', action='store_true',
                        help='sample from generative model')
    parser.add_argument('--sample-proposals', action='store_true',
                        help='sample proposals from discriminative model')
    parser.add_argument('--num-samples', type=int, default=100,
                        help='number of proposal samples')
    parser.add_argument('--use-tokenizer', action='store_true',
                        help='tokenize user input')
    parser.add_argument('--evalb-dir', default='~/EVALB',
                        help='where the evalb excecutable is located')
    parser.add_argument('--out', default='.',
                        help='output to write samples/trees/predictions to')

    args = parser.parse_args()

    if args.mode == 'train':
        train.main(args)
    elif args.mode == 'predict':
        predict.main(args)
    elif args.mode == 'inspect':
        inspect_model.main(args)
    elif args.mode == 'latent':
        latent_model.main(args)


if __name__ == '__main__':
    main()
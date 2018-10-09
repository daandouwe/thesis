#!/usr/bin/env python
import os
import glob
import tempfile
import multiprocessing as mp

import torch
from tqdm import tqdm
from nltk import Tree

from decode import (GreedyDecoder, BeamSearchDecoder, SamplingDecoder,
    GenerativeImportanceDecoder, GenerativeSamplingDecoder)
from eval import evalb
from utils import ceil_div


def remove_duplicates(samples):
    """Filter out duplicate trees from the samples."""
    output = []
    seen = set()
    for tree, proposal_logprob, logprob in samples:
        if tree.linearize() not in seen:
            output.append((tree, proposal_logprob, logprob))
            seen.add(tree.linearize())
    return output


def get_checkfile(checkpoint):
    if not checkpoint:
        latest_dir = max(glob.glob(os.path.join('checkpoints', '*/')))
        return os.path.join(latest_dir, 'model.pt')
    else:
        return checkpoint


def is_tree(line):
    """Simple `oracle` to see if line is a tree."""
    assert isinstance(line, str), line
    try:
        Tree.fromstring(line)
        return True
    except ValueError:
        return False


def predict_file(args):
    assert os.path.exists(args.data), 'specifiy file to parse with --data.'
    print(f'Predicting trees for lines in `{args.data}`.')
    with open(args.data, 'r') as f:
        lines = [line.strip() for line in f.readlines()]
    if is_tree(lines[0]):
        lines = [Tree.fromstring(line).leaves() for line in lines]

    checkfile = get_checkfile(args.checkpoint)
    if args.model == 'disc':
        print('Predicting with discriminative model.')
        decoder = GreedyDecoder(use_tokenizer=False)
        decoder.load_model(path=checkfile)
    elif args.model == 'gen':
        print('Predicting with generative model.')
        decoder = GenerativeImportanceDecoder(use_tokenizer=False)
        decoder.load_model(path=checkfile)
        if args.proposal_model:
            decoder.load_proposal_model(path=args.proposal_model)
        if args.proposal_samples:
            decoder.load_proposal_samples(path=args.proposal_samples)

    print(f'Predicting trees for `{args.data}`...')
    trees = []
    for line in tqdm(lines):
        tree, *rest = decoder(line)
        trees.append(tree)

    # Make a temporay directory for the EVALB files.
    pred_path = os.path.join(args.outdir, 'predicted.txt')
    gold_path = os.path.join(args.outdir, 'gold.txt')
    result_path = os.path.join(args.outdir, 'output.txt')
    # Save the predicted trees.
    with open(pred_path, 'w') as f:
        print('\n'.join(trees), file=f)
    # Also save the gold trees in the temp dir for easy inspection.
    with open(args.data, 'r') as fin:
        with open(gold_path, 'w') as fout:
            print(fin.read(), file=fout, end='')
    # Score the trees.
    fscore = evalb(args.evalb_dir, pred_path, gold_path, result_path)
    print(f'Finished. F-score {fscore:.2f}. Results saved in `{args.outdir}`.')


def predict_input_disc(args):
    print('Predicting with discriminative model.')
    greedy = GreedyDecoder(use_tokenizer=args.use_tokenizer)
    checkfile = get_checkfile(args.checkpoint)
    greedy.load_model(path=checkfile)

    sampler = SamplingDecoder(use_tokenizer=args.use_tokenizer)
    sampler.load_model(path=checkfile)

    while True:
        sentence = input('Input a sentence: ')
        print('Greedy decoder:')
        tree, logprob, *rest = greedy(sentence)
        print('  {} {:.2f}'.format(tree.linearize(with_tag=False), logprob))
        print()

        print('Sampling decoder:')
        for _ in range(3):
            tree, logprob, *rest = sampler(sentence)
            print('  {} {:.2f}'.format(tree.linearize(with_tag=False), logprob))
        print('-'*79)
        print()


def predict_input_gen(args):
    print('Predicting with generative model.')
    assert os.path.exists(args.proposal_model), 'specify valid proposal model.'

    num_samples = 100
    decoder = GenerativeImportanceDecoder(use_tokenizer=True, num_samples=num_samples)
    decoder.load_model(path=args.checkpoint)
    decoder.load_proposal_model(path=args.proposal_model)

    while True:
        sentence = input('Input a sentence: ')

        print('Perplexity: {:.2f}'.format(decoder.perplexity(sentence)))

        print('MAP tree:')
        tree, proposal_logprob, logprob = decoder.map_tree(sentence)
        print('  {} {:.2f} {:.2f}'.format(tree.linearize(with_tag=False), logprob, proposal_logprob))
        print()

        scored = decoder.scored_samples(sentence)
        scored = remove_duplicates(scored)  # For printing purposes.
        print(f'Unique samples: {len(scored)}/{num_samples}.')
        print('Highest q(y|x):')
        scored = sorted(scored, reverse=True, key=lambda t: t[1])
        for tree, proposal_logprob, logprob in scored[:4]:
            print('  {} {:.2f} {:.2f}'.format(tree.linearize(with_tag=False), logprob, proposal_logprob))
        print('Highest p(x,y):')
        scored = sorted(scored, reverse=True, key=lambda t: t[-1])
        for tree, proposal_logprob, logprob in scored[:4]:
            print('  {} {:.2f} {:.2f}'.format(tree.linearize(with_tag=False), logprob, proposal_logprob))
        print('-'*79)
        print()


def sample_generative(args):
    print('Sampling from the generative model.')

    decoder = GenerativeSamplingDecoder()
    decoder.load_model(path=args.checkpoint)

    print('Samples:')
    for i in range(5):
        tree, logprob, _ = decoder()
        print('>', tree.linearize(with_tag=False))
        print()


def sample_proposals_(args):
    assert os.path.exists(args.data), 'specifiy file to parse with --data.'

    print(f'Sampling proposal trees for lines in `{args.data}`.')
    with open(args.data, 'r') as f:
        lines = [line.strip() for line in f.readlines()]
    if is_tree(lines[0]):
        lines = [Tree.fromstring(line).leaves() for line in lines]

    checkfile = get_checkfile(args.checkpoint)
    decoder = SamplingDecoder(use_tokenizer=False)
    decoder.load_model(path=checkfile)

    samples = []
    for i, line in enumerate(tqdm(lines)):
        for _ in range(args.num_samples):
            tree, logprob, _ = decoder(line)  # sample a tree
            samples.append(' ||| '.join((str(i), str(logprob.item()), tree.linearize(with_tag=False))))
    with open(args.out, 'w') as f:
        print('\n'.join(samples), file=f, end='')


def sample_proposals(args):
    assert os.path.exists(args.data), 'specifiy file to parse with --data.'

    print(f'Sampling proposal trees for lines in `{args.data}`.')
    with open(args.data, 'r') as f:
        lines = [line.strip() for line in f.readlines()]
    if is_tree(lines[0]):
        lines = [Tree.fromstring(line).leaves() for line in lines]

    checkfile = get_checkfile(args.checkpoint)
    decoder = SamplingDecoder(use_tokenizer=False)
    decoder.load_model(path=checkfile)

    num_procs = mp.cpu_count() if args.num_procs == -1 else args.num_procs
    if num_procs > 1:
        print(f'Sampling proposals with {num_procs} processors...')
        # Divide the lines among `num_procs` processors.
        chunk_size = ceil_div(len(lines), num_procs)
        partitioned = [lines[i:i+chunk_size] for i in range(0, len(lines), chunk_size)]

        def worker(rank, lines, return_dict):
            """Worker to generate proposal samples."""
            torch.set_num_threads(1)
            all_samples = []
            lines = tqdm(lines) if rank == 0 else lines
            for line in lines:
                samples = []
                for _ in range(args.num_samples):
                    tree, logprob, _ = decoder(line)  # sample a tree
                    samples.append((logprob.item(), tree.linearize(with_tag=False)))
                all_samples.append(samples)
            return_dict[rank] = all_samples

        # Use multiprocessing to parallelize.
        manager = mp.Manager()
        return_dict = manager.dict()
        processes = []
        for rank in range(num_procs):
            p = mp.Process(
                target=worker,
                args=(rank, partitioned[rank], return_dict))
            p.start()
            processes.append(p)
        for p in processes:
            p.join()
        # Join results.
        all_samples = []
        for rank in range(num_procs):
            all_samples.extend(return_dict[rank])
        # Some checks.
        assert len(all_samples) == len(lines), (len(all_samples), len(lines))
        assert all(len(samples) == args.num_samples for samples in all_samples)

        samples = [' ||| '.join((str(i), str(logprob), tree))
            for i, samples in enumerate(all_samples) for logprob, tree in samples]
    else:
        samples = []
        for i, line in enumerate(tqdm(lines)):
            for _ in range(args.num_samples):
                tree, logprob, _ = decoder(line)  # sample a tree
                samples.append(' ||| '.join((str(i), str(logprob.item()), tree.linearize(with_tag=False))))

    # Write samples.
    with open(args.out, 'w') as f:
        print('\n'.join(samples), file=f, end='')


def main(args):
    if args.from_input:
        if args.model == 'disc':
            predict_input_disc(args)
        elif args.model == 'gen':
            predict_input_gen(args)
    elif args.sample_proposals:
        assert args.model == 'disc', 'only discriminative model can generate proposal samples'
        sample_proposals(args)
    elif args.from_file:
        predict_file(args)
    elif args.sample_gen:
        sample_generative(args)
    else:
        exit('Specify type of prediction. Use --from-input, --from-file or --sample-gen.')


if __name__ == '__main__':
    # TODO: Log embeddings while predicting:
    from tensorboardX import SummaryWriter

    writer = SummaryWriter(latest_dir)

    tree = model.stack.tree.linearize() # partial tree
    top_token = model.stack.top_item.token
    embedding = model.stack.top_item.embedding
    encoding = model.stack.top_item.encoding
    writer.add_text('Tree', metadata=[top_token], global_step=t, tag='Encoding')
    writer.add_embedding(embedding, metadata=[top_token], global_step=t, tag='Embedding')
    writer.add_embedding(encoding, metadata=[top_token], global_step=t, tag='Encoding')

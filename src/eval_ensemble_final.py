from pytorch_pretrained_bert import BertTokenizer
from dataset import ReviewDataset, get_data_loaders
from model import OpinioNet

import torch
from torch.utils.data import DataLoader

from tqdm import tqdm
import os
import os.path as osp
import pandas as pd
from dataset import ID2C, ID2P, ID2LAPTOP
from collections import Counter


def eval_epoch(model, dataloader, th):
	model.eval()
	step = 0
	result = []
	pbar = tqdm(dataloader)
	for raw, x, _ in pbar:
		if step == len(dataloader):
			pbar.close()
			break
		rv_raw, _ = raw
		x = [item.cuda() for item in x]
		with torch.no_grad():
			probs, logits = model.forward(x, 'laptop')
			pred_result = model.gen_candidates(probs)
			pred_result = model.nms_filter(pred_result, th)

		result += pred_result

		step += 1
	return result


def accum_result(old, new):
	if old is None:
		return new
	for i in range(len(old)):
		merged = Counter(dict(old[i])) + Counter(dict(new[i]))
		old[i] = list(merged.items())
	return old


def average_result(result, num):
	for i in range(len(result)):
		for j in range(len(result[i])):
			result[i][j] = (result[i][j][0], result[i][j][1] / num)
	return result


def gen_submit(ret, raw):
	result = pd.DataFrame(columns=['id', 'A', 'O', 'C', 'P'])
	cur_idx = 1
	for i, opinions in enumerate(ret):

		if len(opinions) == 0:
			result.loc[result.shape[0]] = {'id': cur_idx, 'A': '_', 'O': '_', 'C': '_', 'P': '_'}

		for j, (opn, score) in enumerate(opinions):
			a_s, a_e, o_s, o_e = opn[0:4]
			c, p = opn[4:6]
			if a_s == 0:
				A = '_'
			else:
				A = raw[i][a_s - 1: a_e]
			if o_s == 0:
				O = '_'
			else:
				O = raw[i][o_s - 1: o_e]
			C = ID2LAPTOP[c]
			P = ID2P[p]
			result.loc[result.shape[0]] = {'id': cur_idx, 'A': A, 'O': O, 'C': C, 'P': P}
		cur_idx += 1
	return result

def gen_label(ret, raw):
	result = pd.DataFrame(
		columns=['id', 'AspectTerms', 'A_start', 'A_end', 'OpinionTerms', 'O_start', 'O_end', 'Categories',
				 'Polarities'])
	cur_idx = 1
	for i, opinions in enumerate(ret):

		if len(opinions) == 0:
			result.loc[result.shape[0]] = {'id': cur_idx,
									'AspectTerms': '_', 'A_start': ' ', 'A_end': ' ',
									'OpinionTerms': '_', 'O_start': ' ', 'O_end': ' ',
									'Categories': '_', 'Polarities': '_'}

		for j, (opn, score) in enumerate(opinions):
			a_s, a_e, o_s, o_e = opn[0:4]
			c, p = opn[4:6]
			if a_s == 0:
				A = '_'
				a_s = ' '
				a_e = ' '
			else:
				A = raw[i][a_s - 1: a_e]
				a_s = str(a_s - 1)
				a_e = str(a_e)
			if o_s == 0:
				O = '_'
				o_s = ' '
				o_e = ' '
			else:
				O = raw[i][o_s - 1: o_e]
				o_s = str(o_s - 1)
				o_e = str(o_e)
			C = ID2LAPTOP[c]
			P = ID2P[p]
			result.loc[result.shape[0]] = {'id': cur_idx,
									'AspectTerms': A, 'A_start': a_s, 'A_end': a_e,
									'OpinionTerms': O, 'O_start': o_s, 'O_end': o_e,
									'Categories': C, 'Polarities': P}
		cur_idx += 1
	return result


import json
import argparse
from config import PRETRAINED_MODELS
if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument('--rv', type=str, default='../data/TEST/Test_reviews.csv')
	parser.add_argument('--lb', type=str, required=False)
	parser.add_argument('--gen_label', action='store_true')
	parser.add_argument('--o', type=str, default='Result')
	parser.add_argument('--bs', type=int, default=64)
	args = parser.parse_args()

	FOLDS = 5
	SAVING_DIR = '../models/'
	THRESH_DIR = '../models/thresh_dict.json'
	if not osp.exists('../submit'):
		os.mkdir('../submit')
	if not osp.exists('../testResults'):
		os.mkdir('../testResults')

	SUBMIT_DIR = osp.join('../submit', args.o+'_submit.csv')
	LABEL_DIR = osp.join('../testResults', args.o+'_label.csv')

	with open(THRESH_DIR, 'r', encoding='utf-8') as f:
		thresh_dict = json.load(f)

	WEIGHT_NAMES, MODEL_NAMES, THRESHS = [], [], []
	for k, v in thresh_dict.items():
		WEIGHT_NAMES.append(k)
		MODEL_NAMES.append(v['name'])
		THRESHS.append(v['thresh'])

	MODELS = list(zip(WEIGHT_NAMES, MODEL_NAMES, THRESHS))
	# tokenizer = BertTokenizer.from_pretrained(PRETRAINED_MODELS['roberta']['path'], do_lower_case=True)
	# test_dataset = ReviewDataset(args.rv, args.lb, tokenizer, 'laptop')
	# test_loader = DataLoader(test_dataset, args.bs, collate_fn=test_dataset.batchify, shuffle=False, num_workers=5)
	ret = None
	raw = None
	num_model = 0
	for weight_name, model_name, thresh in MODELS:
		if not osp.isfile('../models/' + weight_name):
			continue
		num_model += 1
		model_config = PRETRAINED_MODELS[model_name]
		tokenizer = BertTokenizer.from_pretrained(model_config['path'], do_lower_case=True)
		test_dataset = ReviewDataset(args.rv, args.lb, tokenizer, 'laptop')
		test_loader = DataLoader(test_dataset, args.bs, collate_fn=test_dataset.batchify, shuffle=False, num_workers=5)

		if not raw:
			raw = [s[0][0] for s in test_dataset.samples]

		model = OpinioNet.from_pretrained(model_config['path'], version=model_config['version'], focal=model_config['focal'])
		print(weight_name)
		model.load_state_dict(torch.load('../models/' + weight_name))
		model.cuda()
		ret = accum_result(ret, eval_epoch(model, test_loader, thresh))
		del model
	ret = average_result(ret, num_model)
	ret = OpinioNet.nms_filter(ret, 0.35)

	if args.lb:
		result = gen_label(ret, raw)
		result.to_csv(LABEL_DIR, header=True, index=False)
	else:
		result = gen_submit(ret, raw)
		result.to_csv(SUBMIT_DIR, header=False, index=False)
	print(len(result['id'].unique()), result.shape[0])

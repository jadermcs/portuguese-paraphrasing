import nltk; nltk.download('wordnet')

import pandas as pd
import numpy as np
import random
from tqdm import tqdm
from itertools import permutations
from eda import eda
from transformers import (
    BertForSequenceClassification, BertTokenizer,
    TrainingArguments, Trainer
)
from datasets import load_dataset, Dataset, DatasetDict, load_from_disk, load_metric
from sklearn.metrics import precision_recall_fscore_support, accuracy_score

model = BertForSequenceClassification.from_pretrained("distilbert-base-uncased")
tokenizer = BertTokenizer.from_pretrained("distilbert-base-uncased")

data = load_dataset("tapaco", "en")
data['train'].to_csv("tapaco_en.csv", index=False)

sample_data = True

df = pd.read_csv("tapaco_en.csv")
df.drop(columns=["lists", "tags", "language"], inplace=True)
df["paraphrase"] = df["paraphrase"].str.lower()
if sample_data:
  indexes = np.random.choice(df["paraphrase_set_id"].unique(), size=1000)
  df = df[df["paraphrase_set_id"].isin(indexes)]
print(df.shape)
df.tail()

train_indexes = df[df.paraphrase_set_id % 4 != 0].index
valid_indexes = df[df.paraphrase_set_id % 4 == 0].index

def match_pairs(df, index):
    df = df.loc[index]
    df.set_index(['paraphrase_set_id', 'sentence_id'], inplace=True)
    new_df = pd.DataFrame(columns=['id', 'setA', 'setB'])
    for id, group in tqdm(df.groupby(level=0)):
        for seta, setb in permutations(group['paraphrase'], 2):
            new_df = new_df.append({'id': id, 'setA':seta, 'setB':setb}, ignore_index=True)
    return new_df

train_df = match_pairs(df, train_indexes)
valid_df = match_pairs(df, valid_indexes)

def get_other(df):
    df['other'] = np.roll(df['setB'], df.groupby("id").count().max()["setA"])
    return df

train_df = get_other(train_df)
valid_df = get_other(valid_df)

train_df.head(10)

train = Dataset.from_pandas(train_df, split="train")
valid = Dataset.from_pandas(valid_df, split="valid")
data = DatasetDict({"train": train, "valid": valid})
#data.save_to_disk("/content/drive/MyDrive/models/mt5data")

#data = load_from_disk("/content/drive/MyDrive/models/mt5data")

data

def batched_eda(examples):
  return [eda(example, num_aug=1)[0] for example in examples]

def gen_examples(examples):
  len_examples = len(examples["setA"])
  result = {
      "labels": [1]*len_examples + [0]*len_examples,
      "setA": examples["setA"] + examples["setA"].copy(),
      "setB": examples["setB"] + (
            batched_eda(examples["setA"]) if random.random() <= .5 else examples["other"]
            ) # create fake paraphrasing
    }
  return result

data = data.map(
    gen_examples,
    remove_columns=["id", "other"],
    batched=True,
).shuffle()
data

def tokenize(example):
  result = tokenizer(example['setA'], example['setB'], max_length=256,
                  padding="max_length", truncation=True)
  return result

col_names = data['train'].features
data = data.map(
    tokenize,
    remove_columns=["setA", "setB"],
)
data

def compute_metrics(pred):
    labels = pred.label_ids
    preds = pred.predictions.argmax(-1)
    precision, recall, f1, _ = precision_recall_fscore_support(labels, preds, average='binary')
    acc = accuracy_score(labels, preds)
    return {
        'accuracy': acc,
        'f1': f1,
        'precision': precision,
        'recall': recall
    }

args = TrainingArguments(
    "models/bert_fake_paraphrase_detector",
    num_train_epochs=20,
    per_device_train_batch_size=32,
    gradient_accumulation_steps=2,
    save_strategy="no",
    evaluation_strategy="steps",
    eval_steps=100,
    warmup_steps=500,
    weight_decay=0.01,
    report_to="wandb",
)

trainer = Trainer(
    model,
    args=args,
    tokenizer=tokenizer,
    train_dataset=data['train'],
    eval_dataset=data['valid'],
    compute_metrics=compute_metrics,
)

trainer.train()
trainer.save_model()
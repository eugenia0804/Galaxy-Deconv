import os
import json
import logging
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset
from torch.utils.data import DataLoader, random_split


def down_sample(input, rate=4):
    weight = torch.ones([1,1,rate,rate]) / (rate**2) # Average filter.
    input = input.unsqueeze(0).unsqueeze(0)
    output = F.conv2d(input=input, weight=weight, stride=rate).squeeze(0).squeeze(0)
    
    return output


class Galaxy_Dataset(Dataset):
    """Simulated Galaxy Image Dataset inherited from ```torch.utils.data.Dataset```."""
    def __init__(self, data_path='/mnt/WD6TB/tianaoli/dataset/LSST_23.5/', train=True,
                 psf_folder='psf/', obs_folder='obs/', gt_folder='gt/'):
        """Construction function for the PyTorch Galaxy Dataset.

        Args:
            data_path (str, optional): Path to the dataset. Defaults to '/mnt/WD6TB/tianaoli/dataset/LSST_23.5/'.
            train (bool, optional): Whether the dataset is generated for training or testing. Defaults to True.
            psf_folder (str, optional): Path to the PSF image folder. Defaults to 'psf/'.
            obs_folder (str, optional): Path to the observed image folder. Defaults to 'obs/'.
            gt_folder (str, optional): Path to the ground truth image folder. Defaults to 'gt/'.
        """
        super(Galaxy_Dataset, self).__init__()
        
        self.logger = logging.getLogger('Dataset')
        
        # Initialize parameters
        self.data_path = data_path
        self.train= train
        self.psf_folder = psf_folder
        self.obs_folder = obs_folder
        self.gt_folder = gt_folder
        self.n_total, self.n_train, self.n_test = 0, 0, 0
        self.sequence = []
        self.info = {}

        # Read in information
        self.info_file = os.path.join(self.data_path, f'{self.survey}_{self.I}_info.json')
        try:
            with open(self.info_file, 'r') as f:
                self.info = json.load(f)
            self.n_total = self.info['n_total']
            self.n_train = self.info['n_train']
            self.n_test = self.info['n_test']
            self.sequence = self.info['sequence']
            self.logger.info(f"Successfully constructed {'train' if self.train else 'test'} dataset.  Total Samples: {self.n_train if self.train else self.n_test}")
        except:
            self.logger.critical(f'Failed reading information from {self.info_file}.')

    def __len__(self):
        return self.n_train if self.train else self.n_test

    def __getitem__(self, i):
        idx = i if self.train else i + self.n_train
        
        psf_path = os.path.join(self.data_path, self.psf_folder)
        psf = torch.load(os.path.join(psf_path, f"psf_{idx}.pth")).unsqueeze(0)

        obs_path = os.path.join(self.data_path, self.obs_folder)
        obs = torch.load(os.path.join(obs_path, f"obs_{idx}.pth")).unsqueeze(0)

        gt_path = os.path.join(self.data_path, self.gt_folder)
        gt = torch.load(os.path.join(gt_path, f"gt_{idx}.pth")).unsqueeze(0)

        alpha = obs.ravel().mean().float()
        alpha = torch.Tensor(alpha).view(1,1,1)

        return (obs, psf, alpha), gt
            
            
def get_dataloader(data_path='/mnt/WD6TB/tianaoli/dataset/LSST_23.5/', train=True, train_test_split=0.857, batch_size=32,
                   psf_folder='psf/', obs_folder='obs/', gt_folder='gt/'):
    """Generate PyTorch dataloaders for training or testing.

    Args:
        data_path (str, optional): Path the dataset. Defaults to '/mnt/WD6TB/tianaoli/dataset/LSST_23.5/'.
        train (bool, optional): Whether to generate train dataloader or test dataloader. Defaults to True.
        train_test_split (float, optional): Proportion of data used in train dataloader in train dataset, the rest will be used in valid dataloader. Defaults to 0.857.
        batch_size (int, optional): Batch size for training dataset. Defaults to 32.
        psf_folder (str, optional): Path to the PSF image folder. Defaults to 'psf/'.
        obs_folder (str, optional): Path to the observed image folder. Defaults to 'obs/'.
        gt_folder (str, optional): Path to the ground truth image folder. Defaults to 'gt/'.

    Returns:
        train_loader (```torch.utils.data.DataLoader```):  PyTorch dataloader for train dataset.
        val_loader (```torch.utils.data.DataLoader```): PyTorch dataloader for valid dataset.
        test_loader (```torch.utils.data.DataLoader```): PyTorch dataloader for test dataset.
    """
    if train:
        train_dataset = Galaxy_Dataset(data_path=data_path, train=True)
        train_size = int(train_test_split * len(train_dataset))
        val_size = len(train_dataset) - train_size
        train_dataset, val_dataset = random_split(train_dataset, [train_size, val_size])
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
        return train_loader, val_loader
    else:
        test_dataset = Galaxy_Dataset(data_path=data_path, train=False, psf_folder=psf_folder, obs_folder=obs_folder, gt_folder=gt_folder)
        test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False)
        return test_loader
    
if __name__ == '__main__':
    get_dataloader()
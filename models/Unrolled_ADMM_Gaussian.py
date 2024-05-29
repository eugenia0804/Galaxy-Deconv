import numpy as np
import torch
from torch.fft import fft2, ifft2, fftshift, ifftshift
import torch.nn as nn
import torch.nn.functional as F

from models.ResUNet import ResUNet
from models.XDenseUNet import XDenseUNet
from utils.utils_torch import conv_fft_batch, psf_to_otf, crop_half, pad_double



class DoubleConv(nn.Module):
	"""(convolution => [BN] => ReLU) * 2"""
	def __init__(self, in_channels, out_channels, mid_channels=None):
		super(DoubleConv, self).__init__()
		if not mid_channels:
			mid_channels = out_channels
		self.double_conv = nn.Sequential(
			nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1),
			nn.BatchNorm2d(mid_channels),
			nn.ReLU(inplace=True),
			nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1),
			nn.BatchNorm2d(out_channels),
			nn.ReLU(inplace=True)
		)

	def forward(self, x):
		return self.double_conv(x)


class Down(nn.Module):
	"""Downscaling with maxpool then double conv"""
	def __init__(self, in_channels, out_channels):
		super(Down, self).__init__()
		self.maxpool_conv = nn.Sequential(
			nn.MaxPool2d(2),
			DoubleConv(in_channels, out_channels)
		)

	def forward(self, x):
		return self.maxpool_conv(x)


class SubNet(nn.Module):
	def __init__(self, n):
		super(SubNet, self).__init__()
		self.n = n
		self.conv_layers = nn.Sequential(
			Down(1,4),
			Down(4,8),
			Down(8,16),
			Down(16,16))
		self.mlp = nn.Sequential(
			nn.Linear(16*8*8+1, 64),
			nn.ReLU(inplace=True),
			nn.Linear(64, 64),
			nn.ReLU(inplace=True),
			nn.Linear(64, self.n),
			nn.Softplus())
		self.resize = nn.Upsample(size=[256,256], mode='bilinear', align_corners=True)
		
	def forward(self, kernel, alpha):
		N, _, h, w  = kernel.size()
		h1, h2 = int(np.floor(0.5*(128-h))), int(np.ceil(0.5*(128-h)))
		w1, w2 = int(np.floor(0.5*(128-w))), int(np.ceil(0.5*(128-w)))
		k_pad = F.pad(kernel, (w1,w2,h1,h2), "constant", 0)
		H = fft2(ifftshift(k_pad, dim=(-2,-1)))
		HtH = torch.abs(H)**2
		x = self.conv_layers(HtH.float())
		x = torch.cat((x.view(N,1,16*8*8),  alpha.float().view(N,1,1)), axis=2).float()
		output = self.mlp(x) + 1e-6
		return output.view(N, 1, 1, self.n)


class Z_Update_ResUNet(nn.Module):
	"""Updating Z with ResUNet as denoiser."""
	def __init__(self):
		super(Z_Update_ResUNet, self).__init__()		
		self.net = ResUNet(nc=[32, 64, 128, 256])

	def forward(self, z):
		z_out = self.net(z.float())
		return z_out


class X_Update_Gaussian(nn.Module):
	def __init__(self):
		super(X_Update_Gaussian, self).__init__()

	def forward(self, Y, Ht, HtH, z, u, rho):
		lhs = rho + HtH
		rhs = Ht*Y + fft2(ifftshift(pad_double(rho*z-u), dim=(-2,-1)))
		x = fftshift(ifft2(rhs/lhs), dim=(-2,-1)).real
		return crop_half(x)


class Unrolled_ADMM_Gaussian(nn.Module):
	def __init__(self, n_iters=8, denoiser='ResUNet', PnP=True, subnet=True):
		super(Unrolled_ADMM_Gaussian, self).__init__()
		self.n_iters = n_iters # Number of iterations.
		self.PnP = PnP
		self.subnet = subnet
		self.denoiser = denoiser
		self.X = X_Update_Gaussian()
		self.Z = Z_Update_ResUNet()
		if self.subnet:
			self.init = SubNet(self.n_iters)
		else: 
			self.rho_iters = nn.Parameter(torch.ones(size=[self.n_iters,]), requires_grad=True)
  
	def init_l2(self, Y, Ht, HtH, alpha):
		rhs = Y * Ht
		lhs = HtH + (1/alpha)
		x0 = fftshift(ifft2(rhs/lhs), dim=(-2,-1)).real
		return crop_half(x0)
		# return torch.clamp(x0,0,1)

	def forward(self, y, kernel, alpha):
		y = torch.maximum(y, torch.zeros_like(y))
  
		# Generate auxiliary variables for convolution.
		Y = fft2(ifftshift(pad_double(y), dim=(-2,-1)))
		H = fft2(ifftshift(pad_double(kernel), dim=(-2,-1)))
		Ht, HtH = torch.conj(H), torch.abs(H)**2
  
		if self.subnet:
			rho_iters = self.init(kernel, alpha) 	# Hyperparameters.
		z = self.init_l2(Y, Ht, HtH, alpha) # Initialization using Wiener Deconvolution.
		# x_list.append(x)
  
		# Other ADMM variables
		# z = x.clone()
		u = torch.zeros_like(y, device=y.device)
  
		
        # ADMM iterations
		x_list = []
		for i in range(self.n_iters):
			if self.subnet:
				rho = rho_iters[:,:,:,i].view(-1,1,1,1)
			else:
				rho = self.rho_iters[i]
    
			# X, Z updates
			x = self.X(Y, Ht, HtH, z, u, rho)
			z = self.Z(x)
   
			# Lagrangian updates
			u = u + x - z			

			x_list.append(z)

		return x_list[-1]
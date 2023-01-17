import numpy as np
import torch
import torch.fft
import torch.nn as nn
import torch.nn.functional as F
from collections import OrderedDict
from utils.utils_deblur import gauss_kernel, pad, crop
import utils.cadmos_lib as cl 

# functionName implies a torch version of the function
def fftn(x):
	x_fft = torch.fft.fftn(x,dim=[2,3])
	return x_fft

def ifftn(x):
	return torch.fft.ifftn(x,dim=[2,3])

def ifftshift(x):
	# Copied from user vmos1 in a forum - https://github.com/locuslab/pytorch_fft/issues/9
	for dim in range(len(x.size()) - 1, 0, -1):
		x = torch.roll(x, dims=dim, shifts=x.size(dim)//2)
	return x

def conv_fft(H, x):
	if x.ndim > 3: 
		# Batched version of convolution
		Y_fft = fftn(x)*H.repeat([x.size(0),1,1,1])
		y = ifftn(Y_fft)
	if x.ndim == 3:
		# Non-batched version of convolution
		Y_fft = torch.fft.fftn(x, dim=[1,2])*H
		y = torch.fft.ifftn(Y_fft, dim=[1,2])
	return y.real

def conv_fft_batch(H, x):
	"""Batched version of FFT convolution using PyTorch."""
	Y_fft = fftn(x) * H
	y = ifftn(Y_fft).real
	return y

def img_to_tens(x):
	return torch.from_numpy(np.expand_dims( np.expand_dims(x,0),0))

def scalar_to_tens(x):
	return  torch.Tensor([x]).view(1,1,1,1)

def conv_kernel(k, x, mode='cyclic'):
	_ , h, w = x.size()
	h1, w1 = np.shape(k)
	k = torch.from_numpy(np.expand_dims(k,0))
	k_pad, H = psf_to_otf(k.view(1,1,h1,w1), [1,1,h,w])
	H = H.view(1,h,w)
	Ax = conv_fft(H,x)

	return Ax, k_pad

def conv_kernel_symm(k, x):
	_ , h, w = x.size()
	h1, w1 = np.int32(h/2), np.int32(w/2)
	m = nn.ReflectionPad2d( (h1,h1,w1,w1) )
	x_pad = m(x.view(1,1,h,w)).view(1,h+2*h1, w+2*w1)
	k_pad = torch.from_numpy(np.expand_dims(pad(k,[h+2*h1,w+2*w1]),0))
	H = torch.fft.fftn(k_pad, dim=[1,2])
	Ax_pad = conv_fft(H,x_pad)
	Ax = Ax_pad[:,h1:h+h1,w1:w+w1]
	return Ax, k_pad

def psf_to_otf(ker, size):
	
	psf = torch.zeros(size)
	# circularly shift

	center = (ker.shape[2] + 1) // 2
	psf[:, :, :center, :center] = ker[:, :, center:, center:]
	psf[:, :, :center, -center:] = ker[:, :, center:, :center]
	psf[:, :, -center:, :center] = ker[:, :, :center, center:]
	psf[:, :, -center:, -center:] = ker[:, :, :center, :center]
	# compute the otf
	# otf = torch.rfft(psf, 3, onesided=False)
	otf = torch.fft.fftn(psf, dim=[2,3])
	return psf, otf

def laplacian_kernel():
    lap = torch.Tensor([[[[0, 1, 0], 
                        [1, -4, 1], 
                        [0, 1, 0]]]])
    return lap


class MultiScaleLoss(nn.Module):
	def __init__(self, scales=3, norm='L1'):
		super(MultiScaleLoss, self).__init__()
		self.scales = scales
		if norm == 'L1':
			self.loss = nn.L1Loss()
		if norm == 'L2':
			self.loss = nn.MSELoss()

		self.weights = torch.FloatTensor([1/(2**scale) for scale in range(self.scales)])
		self.multiscales = [nn.AvgPool2d(2**scale, 2**scale) for scale in range(self.scales)]

	def forward(self, output, target):
		loss = 0
		for i in range(self.scales):
			output_i, target_i = self.multiscales[i](output), self.multiscales[i](target)
			loss += self.weights[i]*self.loss(output_i, target_i)
			
		return loss


class ShapeConstraint(nn.Module):
    def __init__(self, device, fov_pixels=48, n_shearlet=2):
        super(ShapeConstraint, self).__init__()
        self.mse = nn.MSELoss()
        self.gamma = 1
        U = cl.makeUi(fov_pixels, fov_pixels)
        shearlets, shearlets_adj = cl.get_shearlets(fov_pixels, fov_pixels, n_shearlet)
        # shealret adjoint of U, i.e Psi^{Star}(U)
        self.psu = np.array([cl.convolve_stack(ui, shearlets_adj) for ui in U])
        self.mu = torch.Tensor(cl.comp_mu(self.psu))
        self.mu = torch.Tensor(self.mu).to(device)
        self.psu = torch.Tensor(self.psu).to(device)
        
    def forward(self, output, target):
        loss = self.mse(output, target)
        for i in range(6):
            for j in range(self.psu.shape[1]):
                loss += self.gamma * self.mu[i,j] * (F.l1_loss(output*self.psu[i,j], target*self.psu[i,j]) ** 2) / 2.
        return loss

def rename_state_dict_keys(state_dict):
	new_state_dict = OrderedDict()
	for key, item in state_dict.items():
		new_key = key.partition('.')[2]
		new_state_dict[new_key] = item
	return new_state_dict

if __name__ == "__main__":
    loss_fc = ShapeConstraint()
    output = torch.rand([32,1,48,48])
    target = torch.zeros([32,1,48,48])
    loss = loss_fc(output, target)
    print(loss)
    